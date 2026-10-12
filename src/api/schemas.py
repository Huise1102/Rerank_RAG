"""API 请求/响应模型（只定义对外契约，不含业务逻辑）。

字段名遵循阶段 4 规格：``/query`` 返回 ``{answer, citations, retrieved_docs, latency}``。
所有响应模型都带 ``example``，FastAPI 生成的 ``/docs`` 可直接当接口文档；
时间统一用**毫秒**（前端直接显示，无需换算）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

#: 检索策略（与 src.retrieval.pipeline.VALID_STRATEGIES 保持一致）
StrategyName = Literal["vector", "bm25", "hybrid", "hybrid_rerank"]
#: 分块策略
ChunkStrategyName = Literal["fixed", "recursive"]

DEFAULT_STRATEGY: StrategyName = "hybrid_rerank"



class QueryRequest(BaseModel):
    """POST /query 的请求体。"""

    question: str = Field(
        ..., min_length=1, max_length=2000,
        description="用户问题",
        examples=["学校规章制度的适用范围是怎么规定的？"],
    )
    strategy: StrategyName = Field(
        default=DEFAULT_STRATEGY,
        description="检索策略：vector | bm25 | hybrid | hybrid_rerank",
    )
    chunk_strategy: ChunkStrategyName | None = Field(
        default=None,
        description="分块策略，默认取服务端配置（当前为 recursive）",
    )
    top_k: int | None = Field(
        default=None, ge=1, le=50,
        description="返回的检索片段条数，默认取服务端配置",
    )
    include_contexts: bool = Field(
        default=True,
        description="是否在响应中返回检索片段全文（前端展开原文需要）。"
        "设为 false 可显著减小响应体积",
    )

    @field_validator("question")
    @classmethod
    def _strip_question(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("问题不能为空或全为空白字符")
        return cleaned

    model_config = {
        "json_schema_extra": {
            "example": {"question": "学校规章制度的适用范围是怎么规定的？", "strategy": "hybrid_rerank"}
        }
    }



class RetrievedDoc(BaseModel):
    """单条检索结果。"""

    rank: int = Field(description="最终名次，从 1 开始")
    chunk_id: str = Field(description="文档块唯一标识，可用于溯源")
    text: str = Field(description="文档块原文")
    source: str = Field(description="来源文件名")
    page: int | str = Field(default="", description="页码")
    score: float = Field(description="最终分数（精排分 > 融合分 > 单路分）")
    fused_score: float | None = Field(default=None, description="融合阶段分数（若有）")
    rerank_score: float | None = Field(default=None, description="精排分数（若有）")
    retriever: str = Field(default="", description="命中的检索器，如 hybrid+rerank")
    matched_by: list[str] = Field(
        default_factory=list,
        description="被哪些检索器召回：['bm25','vector'] 表示两路都命中",
    )


class Citation(BaseModel):
    """一条引用来源。"""

    index: int = Field(description="答案中的引用编号，对应 [n]")
    source: str = Field(description="来源文件名")
    page: int | str = Field(default="", description="页码")
    chunk_id: str = Field(default="", description="文档块标识")
    text: str = Field(default="", description="原始片段（前端点击展开显示）")
    label: str = Field(default="", description="人类可读标签，如 '[1] 测试.pdf 第 18 页'")
    count: int = Field(default=1, description="被引用次数")
    score: float | None = Field(default=None, description="该来源的检索分数")


class CitationReport(BaseModel):
    """引用分析报告。"""

    citations: list[Citation] = Field(default_factory=list, description="模型实际引用的来源")
    unused_indices: list[int] = Field(
        default_factory=list, description="送入但未被引用的编号"
    )
    invalid_indices: list[int] = Field(
        default_factory=list,
        description="模型引用了但不存在的编号（往往伴随幻觉，可用于质量监控）",
    )
    is_refusal: bool = Field(default=False, description="答案是否为「资料不足无法回答」")
    coverage: float = Field(default=0.0, description="被引用的片段占送入片段的比例")
    summary: str = Field(default="", description="一句话摘要")


class LatencyBreakdown(BaseModel):
    """分步耗时（毫秒）。"""

    retrieve_ms: float = Field(default=0.0, description="检索总耗时")
    recall_ms: float = Field(default=0.0, description="召回阶段（含向量/BM25/融合）")
    rerank_ms: float = Field(default=0.0, description="精排阶段")
    generate_ms: float = Field(default=0.0, description="大模型生成")
    total_ms: float = Field(default=0.0, description="端到端总耗时")
    first_token_ms: float | None = Field(
        default=None, description="流式模式下的首 token 延迟"
    )


class QueryResponse(BaseModel):
    """POST /query 的响应体。"""

    answer: str = Field(description="生成的答案")
    citations: CitationReport = Field(description="引用来源与溯源信息")
    retrieved_docs: list[RetrievedDoc] = Field(
        default_factory=list, description="检索到的文档块及得分"
    )
    latency: LatencyBreakdown = Field(description="分步耗时")
    strategy: StrategyName = Field(description="实际使用的检索策略")
    chunk_strategy: str = Field(default="", description="实际使用的分块策略")
    model: str = Field(default="", description="实际使用的生成模型")
    usage: dict[str, Any] = Field(default_factory=dict, description="token 用量")
    warnings: list[str] = Field(default_factory=list, description="告警（降级、截断等）")

    model_config = {
        "json_schema_extra": {
            "example": {
                "answer": "相关异议应在收到通知之日起规定期限内提出书面申请[1]。",
                "citations": {
                    "citations": [{"index": 1, "source": "测试.pdf", "page": 18,
                                   "label": "[1] 测试.pdf 第 18 页"}],
                    "is_refusal": False, "coverage": 0.2, "summary": "引用 1 条来源",
                },
                "retrieved_docs": [],
                "latency": {"retrieve_ms": 2100, "rerank_ms": 18000, "generate_ms": 3000,
                            "total_ms": 23100},
                "strategy": "hybrid_rerank",
            }
        }
    }


class RetrieveRequest(BaseModel):
    """POST /retrieve 的请求体（只检索不生成，供前端调试面板使用）。"""

    question: str = Field(..., min_length=1, max_length=2000)
    strategy: StrategyName = Field(default=DEFAULT_STRATEGY)
    chunk_strategy: ChunkStrategyName | None = None
    top_k: int | None = Field(default=None, ge=1, le=100)


class RetrieveResponse(BaseModel):
    """POST /retrieve 的响应体，带两路召回的明细。"""

    question: str
    strategy: StrategyName
    chunk_strategy: str = ""
    docs: list[RetrievedDoc] = Field(default_factory=list)
    stats: dict[str, Any] = Field(
        default_factory=dict,
        description="召回统计：dense/sparse/overlap/dense_only/sparse_only",
    )
    timing: dict[str, float] = Field(default_factory=dict, description="分步耗时（秒）")
    warnings: list[str] = Field(default_factory=list)



class IngestedFile(BaseModel):
    """本次会话处理（或跳过）的单个文件。"""

    file_name: str
    action: Literal["added", "updated", "skipped", "failed"] = Field(
        description="新增 / 内容变化后重灌 / 内容未变跳过 / 处理失败"
    )
    pages: int = Field(default=0, description="解析出的有效页数")
    chunks: int = Field(default=0, description="生成的文档块数")
    chars: int = Field(default=0, description="字符数")
    reason: str = Field(default="", description="跳过或失败的原因")
    seconds: float = Field(default=0.0, description="该文件处理耗时")


class IngestRequest(BaseModel):
    """POST /ingest 的表单参数说明（实际通过 multipart 上传）。"""

    strategy: ChunkStrategyName | None = Field(
        default=None, description="分块策略，默认取服务端配置"
    )
    force: bool = Field(
        default=False,
        description="为 true 时忽略文件指纹，强制重新解析所有文件",
    )


class IngestResponse(BaseModel):
    """POST /ingest 的响应体。

    入库可能耗时数分钟，故立即返回 task_id，由前端轮询进度接口。
    """

    task_id: str = Field(description="任务标识，用于轮询进度")
    status: Literal["pending", "running", "done", "failed"] = Field(description="任务状态")
    accepted_files: list[str] = Field(default_factory=list, description="已接收的文件名")
    message: str = Field(default="", description="提示信息")
    status_url: str = Field(default="", description="进度查询地址")


class IngestStatus(BaseModel):
    """GET /ingest/status/{task_id} 的响应体。"""

    task_id: str
    status: Literal["pending", "running", "done", "failed"]
    stage: str = Field(default="", description="当前阶段描述")
    progress: float = Field(default=0.0, ge=0.0, le=1.0, description="进度 0~1")
    files: list[IngestedFile] = Field(default_factory=list)
    total_chunks: int = Field(default=0, description="入库后索引总条数")
    started_at: str = Field(default="")
    finished_at: str = Field(default="")
    seconds: float = Field(default=0.0)
    error: str = Field(default="", description="失败原因")


class IndexStatus(BaseModel):
    """索引现状摘要（供 /health 与前端展示）。"""

    chunk_strategy: str
    chunks_in_file: int = Field(default=0, description="分块文件中记录条数")
    vector_count: int = Field(default=0, description="向量库条数")
    bm25_count: int = Field(default=0, description="BM25 索引条数")
    collection: str = Field(default="")
    consistent: bool = Field(
        default=False, description="三个数字是否一致（不一致说明索引与分块不同步）"
    )



class ComponentStatus(BaseModel):
    """单个组件的就绪状态。"""

    name: str
    ready: bool
    detail: str = Field(default="", description="说明或错误原因")


class HealthResponse(BaseModel):
    """GET /health 的响应体。"""

    status: Literal["ok", "degraded", "error"] = Field(
        description="ok=全部就绪；degraded=部分组件不可用但服务仍可响应；error=不可用"
    )
    components: list[ComponentStatus] = Field(default_factory=list)
    index: IndexStatus | None = Field(default=None, description="当前索引现状")
    warmed_up: bool = Field(default=False, description="检索管线是否已预热")
    warmup_seconds: float = Field(default=0.0, description="预热耗时")
    version: str = Field(default="", description="应用版本")


class StrategyOption(BaseModel):
    """可选策略及其说明（前端下拉框直接用）。"""

    value: str
    label: str
    description: str = ""


class StrategiesResponse(BaseModel):
    """GET /strategies 的响应体。"""

    retrieval: list[StrategyOption] = Field(default_factory=list)
    chunk: list[StrategyOption] = Field(default_factory=list)
    default_retrieval: str = ""
    default_chunk: str = ""
