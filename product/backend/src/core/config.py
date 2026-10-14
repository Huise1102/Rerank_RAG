"""
产品级领域知识库系统 —— 全局配置。

沿用参考项目 demo/config.py 的结构与命名规则（分组 + ``RAG_<分组>__<字段>`` 覆盖），
在其基础上：
  - 路径基准改为 product/backend 根（与 demo/ 完全隔离，不指向 demo/indexes、demo/data）
  - 新增基础设施分组：database / redis / minio / storage
  - 去掉评估分组（新项目不做评估）

分层覆盖：默认值（本文件） < .env < 系统环境变量 < 运行时显式传参。
模块导入时自动创建 data/、indexes/、storage/、logs/。
自检：python -m src.core.config
"""

from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: 后端项目根：src/core/config.py -> src/core -> src -> backend
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]


def _abs(p: str | Path) -> Path:
    """把可能相对的磁盘路径展开为绝对路径（以 PROJECT_ROOT 为基准）。"""
    path = Path(p).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


DeviceType = Literal["auto", "cpu", "cuda", "mps"]
LLMProvider = Literal["deepseek", "openai", "custom"]
EmbeddingProvider = Literal["huggingface", "local", "api"]
FusionStrategy = Literal["dense", "sparse", "rrf", "weighted"]
RerankBackend = Literal["cross_encoder", "flag_embedding"]
PDFEngine = Literal["pdfplumber", "pymupdf"]
BM25Tokenizer = Literal["jieba", "char", "whitespace"]
StorageType = Literal["local", "minio"]

#: DeepSeek 密钥格式：``sk-`` 后接 20 位以上字母数字。
_API_KEY_RE = re.compile(r"^sk-[A-Za-z0-9]{20,}$")


class _Section(BaseModel):
    """配置分组基类。extra="forbid" 防止 .env 变量名拼错后被静默忽略。"""

    model_config = {"extra": "forbid", "validate_assignment": True, "protected_namespaces": ()}


# =============================================================================
# 基础设施分组
# =============================================================================


class PathSettings(_Section):
    """数据、索引、存储、日志目录；相对路径以 PROJECT_ROOT 为基准展开。"""

    data_dir: Path = PROJECT_ROOT / "data"
    raw_dir: Path = PROJECT_ROOT / "data" / "raw"
    processed_dir: Path = PROJECT_ROOT / "data" / "processed"

    indexes_dir: Path = PROJECT_ROOT / "indexes"
    chroma_dir: Path = PROJECT_ROOT / "indexes" / "chroma"
    bm25_dir: Path = PROJECT_ROOT / "indexes" / "bm25"

    storage_dir: Path = PROJECT_ROOT / "storage"
    logs_dir: Path = PROJECT_ROOT / "logs"

    @field_validator("*", mode="after")
    @classmethod
    def _expand(cls, v: Path) -> Path:
        return _abs(v)

    def all_dirs(self) -> list[Path]:
        return [
            self.data_dir,
            self.raw_dir,
            self.processed_dir,
            self.indexes_dir,
            self.chroma_dir,
            self.bm25_dir,
            self.storage_dir,
            self.logs_dir,
        ]


class DatabaseSettings(_Section):
    """MySQL 连接（异步驱动 aiomysql）。"""

    url: str = Field(
        default="mysql+aiomysql://rag:rag123456@127.0.0.1:3306/rag_system?charset=utf8mb4",
        description="SQLAlchemy 异步连接串",
    )
    echo: bool = Field(default=False, description="是否打印 SQL")
    pool_size: int = Field(default=10, ge=1)
    max_overflow: int = Field(default=20, ge=0)
    pool_recycle: int = Field(default=3600, ge=-1, description="连接回收秒数，避免 MySQL 8 小时断连")


class RedisSettings(_Section):
    """Redis：Celery broker/backend、会话缓存、限流。"""

    url: str = "redis://127.0.0.1:6379/0"
    cache_ttl: int = Field(default=3600, ge=1, description="会话缓存过期秒数")


class MinioSettings(_Section):
    """MinIO 对象存储（STORAGE_TYPE=minio 时启用）。"""

    endpoint: str = "127.0.0.1:9000"
    access_key: str = "minioadmin"
    secret_key: str = "minioadmin"
    bucket: str = "rag-documents"
    secure: bool = False


class StorageSettings(_Section):
    """文件存储后端选择：local（本地 storage/，毕设默认）| minio。"""

    type: StorageType = "local"


# =============================================================================
# AI 链路分组（与 demo 同名同义，保证复制过来的模块零改动可用）
# =============================================================================


class StrategySpec(_Section):
    """单个分块策略的参数。"""

    chunk_size: int = Field(ge=32, le=4000, description="目标块长度（字符数）")
    chunk_overlap: int = Field(ge=0, le=2000, description="相邻块重叠字符数")
    separators: list[str] = Field(
        default_factory=lambda: ["\n\n", "\n", "。", " ", ""],
        description="递归切分分隔符优先级",
    )
    min_chunk_size: int = Field(default=80, ge=1, description="小于该长度的碎块并入相邻块")
    max_chunk_size: int | None = Field(default=None, description="硬上限")

    @model_validator(mode="after")
    def _check(self) -> "StrategySpec":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"chunk_overlap({self.chunk_overlap}) 必须小于 chunk_size({self.chunk_size})"
            )
        if self.max_chunk_size is not None and self.max_chunk_size < self.chunk_size:
            raise ValueError("max_chunk_size 不应小于 chunk_size")
        return self


class ChunkSettings(_Section):
    """分块策略：fixed（固定长度）/ recursive（递归语义）。参数与 demo 一致。"""

    default_strategy: str = Field(default="recursive", description="默认分块策略名")

    fixed: StrategySpec = Field(
        default_factory=lambda: StrategySpec(chunk_size=500, chunk_overlap=50, max_chunk_size=800),
    )
    recursive: StrategySpec = Field(
        default_factory=lambda: StrategySpec(
            chunk_size=500,
            chunk_overlap=50,
            separators=["\n\n", "\n", "。", " ", ""],
            max_chunk_size=1200,
        ),
    )
    strip_whitespace: bool = True
    wrap_gap_ratio: float = Field(default=1.6, gt=1.0, le=4.0)
    wrap_indent_tolerance: float = Field(default=2.0, ge=0.0)
    wrap_indent_ratio: float = Field(default=1.5, gt=1.0, le=5.0)
    remove_page_number: bool = Field(default=True)
    clean_headers_footers: bool = Field(default=True)
    header_footer_min_ratio: float = Field(default=0.5, ge=0.0, le=1.0)
    header_footer_margin: int = Field(default=3, ge=1)
    header_zone_ratio: float = Field(default=0.08, gt=0.0, le=0.3)
    footer_zone_ratio: float = Field(default=0.12, gt=0.0, le=0.3)
    header_footer_max_length: int = Field(default=60, ge=4)
    strip_cjk_spaces: bool = Field(default=False)
    extra_header_footer_patterns: list[str] = Field(default_factory=list)
    metadata_fields: list[str] = Field(
        default=[
            "source", "file_name", "page", "total_pages", "chunk_id",
            "chunk_index", "total_chunks", "strategy", "doc_type",
        ]
    )

    def for_strategy(self, strategy: str | None = None) -> StrategySpec:
        name = (strategy or self.default_strategy).strip().lower()
        if name not in ("fixed", "recursive"):
            raise ValueError(f"未知分块策略 '{strategy}'，可选：fixed | recursive")
        return getattr(self, name)

    @property
    def available_strategies(self) -> list[str]:
        return ["fixed", "recursive"]

    @property
    def splitter(self) -> str:
        return self.default_strategy


class PDFSettings(_Section):
    """PDF 文本抽取（pdfplumber）。"""

    engine: PDFEngine = "pdfplumber"
    use_text_flow: bool = False
    line_tolerance: float = Field(default=3.0, gt=0)
    skip_invalid: bool = Field(default=True)
    keep_empty_pages: bool = False


class EmbeddingSettings(_Section):
    """BAAI/bge-large-zh-v1.5 本地向量化。"""

    provider: EmbeddingProvider = "huggingface"
    model_name: str = "BAAI/bge-large-zh-v1.5"
    device: DeviceType = "auto"
    normalize_embeddings: bool = Field(default=True)
    batch_size: int = Field(default=16, ge=1)
    max_length: int = Field(default=512, ge=64, le=1024)
    query_instruction: str = "为这个句子生成表示以用于检索相关文章："
    use_query_instruction: bool = Field(default=True)
    show_progress: bool = False
    cache_dir: Path | None = Field(default=None)
    offline: bool = Field(default=True, description="只用本地缓存，不访问 HuggingFace")
    dimension: int = Field(default=1024, ge=1)

    @field_validator("cache_dir", mode="after")
    @classmethod
    def _expand_cache(cls, v: Path | None) -> Path | None:
        return _abs(v) if v is not None else None

    @property
    def resolved_model(self) -> str:
        p = Path(self.model_name)
        if p.is_absolute() and p.exists():
            return str(p)
        candidate = PROJECT_ROOT / p
        if candidate.exists():
            return str(candidate)
        return self.model_name


class RerankSettings(_Section):
    """BAAI/bge-reranker-v2-m3 精排。"""

    enabled: bool = Field(default=True)
    model_name: str = "BAAI/bge-reranker-v2-m3"
    backend: RerankBackend = "cross_encoder"
    device: DeviceType = "auto"
    top_k: int = Field(default=5, ge=1, le=50, description="精排后保留条数")
    candidate_multiplier: int = Field(default=2, ge=1, le=5)
    batch_size: int = Field(default=8, ge=1)
    max_length: int = Field(default=512, ge=64, le=1024)
    normalize_score: bool = False
    score_threshold: float = 0.0
    use_fp16: bool = True

    @property
    def resolved_model(self) -> str:
        p = Path(self.model_name)
        if p.is_absolute() and p.exists():
            return str(p)
        candidate = PROJECT_ROOT / p
        if candidate.exists():
            return str(candidate)
        return self.model_name


class RetrievalSettings(_Section):
    """BM25 稀疏 + Chroma 稠密 + RRF 融合。链路参数与 demo 完全一致。"""

    top_k: int = Field(default=20, ge=1, le=200, description="融合后条数 = 精排候选池大小")
    dense_top_k: int = Field(default=50, ge=1, le=200, description="向量单独召回条数")
    sparse_top_k: int = Field(default=50, ge=1, le=200, description="BM25 单独召回条数")

    fusion: FusionStrategy = Field(default="rrf")
    dense_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    sparse_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    rrf_k: int = Field(default=60, ge=1)

    bm25_tokenizer: BM25Tokenizer = "jieba"
    bm25_k1: float = Field(default=1.5, ge=0.0, le=3.0)
    bm25_b: float = Field(default=0.75, ge=0.0, le=1.0)
    bm25_max_df_ratio: float = Field(default=0.6, gt=0.0, le=1.0)
    bm25_min_term_df: int = Field(default=1, ge=1)
    bm25_score_floor: float = Field(default=0.5, ge=0.0)

    deduplicate: bool = Field(default=True)
    merge_adjacent: bool = False
    min_score: float = Field(default=0.0)

    @model_validator(mode="after")
    def _check_weights(self) -> "RetrievalSettings":
        if self.fusion == "weighted" and abs(self.dense_weight + self.sparse_weight - 1.0) > 1e-6:
            raise ValueError("weighted 融合下 dense_weight+sparse_weight 必须为 1")
        return self


class LLMSettings(_Section):
    """DeepSeek OpenAI 兼容接口。"""

    provider: LLMProvider = "deepseek"
    api_key: str | None = Field(default=None)
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    fallback_models: list[str] = Field(default=["deepseek-chat"])

    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, gt=0.0, le=1.0)
    max_tokens: int = Field(default=2048, ge=64)
    timeout: int = Field(default=120, ge=1)
    max_retries: int = Field(default=3, ge=0, le=10)
    stream: bool = Field(default=True)

    @field_validator("base_url", mode="after")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def is_configured(self) -> bool:
        return bool(_API_KEY_RE.match((self.api_key or "").strip()))

    def require_api_key(self) -> str:
        raw = (self.api_key or "").strip()
        if not _API_KEY_RE.match(raw):
            raise RuntimeError(
                "DeepSeek API Key 不可用：请在 .env 中填写 RAG_LLM__API_KEY=sk-你的真实密钥"
            )
        return raw


class GenerationSettings(_Section):
    """上下文拼装与 LLM 输出约束。"""

    context_max_chars: int = Field(default=6000, ge=500, description="注入 prompt 的上下文字符上限")
    include_scores: bool = False
    include_source: bool = True
    citation_style: Literal["bracket", "inline", "none"] = "bracket"
    history_max_turns: int = Field(default=3, ge=0)

    system_prompt: str = (
        "你是一个严谨的领域知识问答助手。请遵守以下规则：\n"
        "1. 只能依据【参考资料】回答问题，不得使用参考资料之外的知识，不得臆测或编造。\n"
        "2. 如果参考资料不足以回答问题，必须直接回答“根据现有资料无法回答该问题”，"
        "并说明还缺少哪些信息，禁止强行作答。\n"
        "3. 回答使用中文，结构清晰；涉及步骤或对比时优先使用分点或表格。\n"
        "4. 不要重复参考资料原文，应进行归纳与整合。\n"
        "5. 若【参考资料】中的多条规定不一致（例如不同文件对同一事项规定了不同期限），"
        "应如实指出存在冲突并分别列明，不要只挑一条作答。"
    )
    user_prompt_template: str = (
        "【参考资料】\n{context}\n\n"
        "【用户问题】\n{question}\n\n"
        "请依据上述参考资料回答，并标注引用编号。"
    )
    no_context_reply: str = "抱歉，知识库中未检索到与该问题相关的内容，请尝试换一种问法或补充背景信息。"


class APISettings(_Section):
    """FastAPI 服务。"""

    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    reload: bool = False
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(
        default=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"]
    )
    request_timeout: int = Field(default=180, ge=1)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    warmup_on_startup: bool = Field(default=True, description="启动时后台线程预热检索与精排模型")


class LogSettings(_Section):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    dir: Path = PROJECT_ROOT / "logs"
    file_name: str = "app.log"
    console: bool = True
    max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    backup_count: int = Field(default=3, ge=0)

    @field_validator("dir", mode="after")
    @classmethod
    def _expand_log(cls, v: Path) -> Path:
        return _abs(v)


# =============================================================================
# 聚合对象
# =============================================================================


class AppSettings(BaseSettings):
    """全局配置聚合对象；业务代码统一 ``from src.core.config import settings`` 使用。"""

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        env_prefix="RAG_",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
        validate_default=True,
    )

    domain: str = Field(default="法律领域", description="问答系统面向的领域")
    language: str = "zh"
    debug: bool = False

    #: 种子数据来源：仓库根 resource/（只读引用，不复制、不改写）
    resource_dir: Path = Field(default=Path(r"C:\Users\98429\Desktop\基于混合检索与Rerank的领域知识库系统\resource"))

    # 基础设施
    paths: PathSettings = Field(default_factory=PathSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    minio: MinioSettings = Field(default_factory=MinioSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)

    # AI 链路
    chunk: ChunkSettings = Field(default_factory=ChunkSettings)
    pdf: PDFSettings = Field(default_factory=PDFSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    rerank: RerankSettings = Field(default_factory=RerankSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    generation: GenerationSettings = Field(default_factory=GenerationSettings)
    api: APISettings = Field(default_factory=APISettings)
    log: LogSettings = Field(default_factory=LogSettings)

    @field_validator("resource_dir", mode="after")
    @classmethod
    def _expand_resource(cls, v: Path) -> Path:
        return _abs(v)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def project_root(self) -> str:
        return str(PROJECT_ROOT)

    @property
    def rerank_candidate_k(self) -> int:
        """送入 reranker 的候选条数（≥ rerank.top_k）。"""
        return max(self.retrieval.top_k * self.rerank.candidate_multiplier, self.rerank.top_k)

    def ensure_dirs(self) -> list[Path]:
        """创建所有必需目录（幂等）。"""
        created: list[Path] = []
        for d in self.paths.all_dirs():
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
                created.append(d)
        return created

    def apply_environment(self) -> None:
        """把配置回写到 os.environ，供第三方库（HF / torch / chromadb）读取。"""
        if self.embedding.cache_dir is not None:
            os.environ.setdefault("HF_HOME", str(self.embedding.cache_dir))
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
        if self.embedding.offline:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    def to_dict(self, hide_secrets: bool = True) -> dict[str, Any]:
        """导出为普通字典；hide_secrets=True 时脱敏密钥。"""
        data = self.model_dump(mode="json")
        if hide_secrets:
            if data.get("llm", {}).get("api_key"):
                key = str(data["llm"]["api_key"])
                data["llm"]["api_key"] = f"{key[:6]}...{key[-4:]}" if len(key) > 12 else "***"
            for group in ("database", "redis", "minio"):
                blob = data.get(group, {})
                for field_name in ("url", "secret_key", "access_key"):
                    if blob.get(field_name):
                        blob[field_name] = "***"
        return data

    def summary(self) -> str:
        r, rk, llm = self.retrieval, self.rerank, self.llm
        return (
            f"[配置] 领域={self.domain} | 分块={self.chunk.for_strategy().chunk_size}/"
            f"{self.chunk.for_strategy().chunk_overlap} | 检索 top_k={r.top_k} 融合={r.fusion} "
            f"| Rerank={'on' if rk.enabled else 'off'} top_k={rk.top_k} "
            f"| LLM={llm.model}(key={'已配置' if llm.is_configured else '未配置'}) "
            f"| device={self.embedding.device} | storage={self.storage.type}"
        )


def _silence_chroma_telemetry_noise() -> None:
    """屏蔽 chromadb 0.5.x 的遥测报错噪音（posthog 版本签名不兼容）。"""
    logging.getLogger("chromadb.telemetry.product.posthog").disabled = True
    logging.getLogger("chromadb.telemetry").setLevel(logging.CRITICAL)


def configure_chroma_telemetry() -> None:
    """双重保险关闭 Chroma 遥测。调用时机：使用 Chroma 之前。"""
    _silence_chroma_telemetry_noise()
    try:
        from chromadb.config import Settings as _ChromaSettings

        _ChromaSettings().anonymized_telemetry = False
    except Exception:  # noqa: BLE001 - 遥测失败不应影响主流程
        pass


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """获取全局配置单例（带缓存）。"""
    cfg = AppSettings()
    cfg.ensure_dirs()
    cfg.apply_environment()
    _silence_chroma_telemetry_noise()
    return cfg


#: 全局唯一配置对象，业务代码统一导入它
settings: AppSettings = get_settings()


def _main() -> None:
    """自检入口：python -m src.core.config"""
    cfg = AppSettings()
    created = cfg.ensure_dirs()
    cfg.apply_environment()
    print("=" * 78)
    print(" 领域知识库系统（product/backend）—— 配置自检")
    print("=" * 78)
    print(f"PROJECT_ROOT : {PROJECT_ROOT}")
    print(f".env 存在    : {(PROJECT_ROOT / '.env').exists()}")
    print(f"resource_dir : {cfg.resource_dir} (存在={cfg.resource_dir.exists()})")
    print("-" * 78)
    print(cfg.summary())
    print("-" * 78)
    print("[目录检查]")
    for d in cfg.paths.all_dirs():
        print(f"  [{'新建' if d in created else '已存在'}] {d}")
    print("-" * 78)
    print(f"LLM API Key : {'✅ 已配置' if cfg.llm.is_configured else '⚠️ 未配置'}")
    print("-" * 78)
    print("[完整配置 JSON]")
    print(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2))
    print("=" * 78)


if __name__ == "__main__":
    _main()
