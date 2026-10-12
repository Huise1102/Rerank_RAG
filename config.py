"""
基于混合检索与 Rerank 的 RAG 领域问答系统 —— 全局配置。
分层覆盖：默认值（本文件） < .env < 系统环境变量 < 运行时显式传参。
路径类字段以 PROJECT_ROOT 为基准展开为绝对路径，与启动目录解耦。
模块导入时自动创建 data/、indexes/、logs/。
环境变量命名：RAG_ + 分组名 + __ + 字段名（双下划线分隔嵌套层级），
如 RAG_RETRIEVAL__TOP_K -> settings.retrieval.top_k。
自检：python config.py
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

#: project/ 目录的绝对路径（本文件所在目录）
PROJECT_ROOT: Path = Path(__file__).resolve().parent


def _abs(p: str | Path) -> Path:
    """把可能是相对的磁盘路径展开为绝对路径（以 PROJECT_ROOT 为基准）。"""
    path = Path(p).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


DeviceType = Literal["auto", "cpu", "cuda", "mps"]
LLMProvider = Literal["deepseek", "openai", "custom"]
EmbeddingProvider = Literal["huggingface", "local", "api"]
FusionStrategy = Literal["dense", "sparse", "rrf", "weighted"]
RerankBackend = Literal["cross_encoder", "flag_embedding"]
SplitterMethod = Literal["recursive", "token"]
PDFEngine = Literal["pdfplumber", "pymupdf"]
BM25Tokenizer = Literal["jieba", "char", "whitespace"]

#: DeepSeek 密钥格式：``sk-`` 后接 20 位以上字母数字（真实密钥长度远超此下限）。
#: 用正面格式校验而不是"排除占位符"，才能挡住用户自定义的占位文字。
_API_KEY_RE = re.compile(r"^sk-[A-Za-z0-9]{20,}$")

#: 形如 ``sk-xxxxxxxx…`` 的占位符：密钥体由同一个字符重复组成。
#: 真实密钥不可能如此，但形状上能通过纯格式校验，因此需要单独排除。
_API_KEY_PLACEHOLDER_RE = re.compile(r"^sk-([A-Za-z0-9])\1+$")



class _Section(BaseModel):
    """配置分组基类。

    extra="forbid" 防止 .env 变量名拼错后被静默忽略；
    protected_namespaces=() 解除 pydantic 对 ``model_`` 前缀的保护，
    以便使用 model_name 字段。
    """

    model_config = {"extra": "forbid", "validate_assignment": True, "protected_namespaces": ()}


class PathSettings(_Section):
    """数据、索引、日志目录；相对路径以 PROJECT_ROOT 为基准展开。"""

    data_dir: Path = PROJECT_ROOT / "data"
    raw_dir: Path = PROJECT_ROOT / "data" / "raw"
    processed_dir: Path = PROJECT_ROOT / "data" / "processed"
    eval_dir: Path = PROJECT_ROOT / "data" / "eval"

    indexes_dir: Path = PROJECT_ROOT / "indexes"
    chroma_dir: Path = PROJECT_ROOT / "indexes" / "chroma"
    bm25_dir: Path = PROJECT_ROOT / "indexes" / "bm25"

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
            self.eval_dir,
            self.indexes_dir,
            self.chroma_dir,
            self.bm25_dir,
            self.logs_dir,
        ]


class StrategySpec(_Section):
    """单个分块策略的参数。两种策略各自独立保存参数，便于消融对比。"""

    chunk_size: int = Field(ge=32, le=4000, description="目标块长度（字符数）")
    chunk_overlap: int = Field(ge=0, le=2000, description="相邻块重叠字符数")
    separators: list[str] = Field(
        default_factory=lambda: ["\n\n", "\n", "。", " ", ""],
        description="递归切分分隔符优先级（固定长度策略不使用该参数）",
    )
    min_chunk_size: int = Field(default=80, ge=1, description="小于该长度的碎块将并入相邻块")
    max_chunk_size: int | None = Field(
        default=None, description="硬上限；None 表示不额外限制（由 chunk_size 控制）"
    )

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
    """阶段 1 使用：PDF -> 文本 -> 分块。

    两种可切换策略，参数各自独立保存：``fixed`` 按字符窗口滑动切分；
    ``recursive`` 用 RecursiveCharacterTextSplitter，分隔符
    ["\\n\\n","\\n","。"," ",""]，优先在段落/句子边界断开。
    业务代码用 ``settings.chunk.for_strategy(名称)`` 取参数。
    """

    #: 默认策略（可用环境变量 RAG_CHUNK__DEFAULT_STRATEGY=fixed 切换）
    default_strategy: str = Field(default="recursive", description="默认使用的分块策略名")

    fixed: StrategySpec = Field(
        default_factory=lambda: StrategySpec(chunk_size=500, chunk_overlap=50, max_chunk_size=800),
        description="策略 a：固定长度分块",
    )
    recursive: StrategySpec = Field(
        default_factory=lambda: StrategySpec(
            chunk_size=500,
            chunk_overlap=50,
            separators=["\n\n", "\n", "。", " ", ""],
            max_chunk_size=1200,
        ),
        description="策略 b：递归语义分块",
    )
    strip_whitespace: bool = True
    #: 段落重组参数（loader 把 PDF 的视觉折行还原成逻辑段落）
    wrap_gap_ratio: float = Field(
        default=1.6, gt=1.0, le=4.0,
        description="行间距超过「行高×该倍数」时判定为段落间距，插入空行",
    )
    wrap_indent_tolerance: float = Field(
        default=2.0, ge=0.0,
        description="判定缩进差异的容差（磅）",
    )
    wrap_indent_ratio: float = Field(
        default=1.5, gt=1.0, le=5.0,
        description=(
            "下一行缩进超过「上一行缩进×该倍数」时视为新段落（公文首行缩进特征）；"
            "反之缩进变小说明是续行，必须合并"
        ),
    )
    remove_page_number: bool = Field(default=True, description="移除独立成行的页码，如「- 12 -」「第 3 页」")
    clean_headers_footers: bool = Field(default=True, description="自动识别并移除重复出现的页眉页脚")
    header_footer_min_ratio: float = Field(
        default=0.5, ge=0.0, le=1.0, description="某行在多少比例的页面上重复出现即判定为页眉/页脚"
    )
    header_footer_margin: int = Field(
        default=3, ge=1, description="缺少坐标信息时，退化模式下参与判定的首尾行数"
    )
    #: 分区比例要按真实版面取值，取太大会把正文标题误判为页眉。
    #: 实测：A4 页眉绘制在 top≈33（占页高 4%），正文标题从 top≈70（8%）开始，
    #: 因此页眉区取 5% 左右才既能覆盖页眉、又不误伤正文标题。
    header_zone_ratio: float = Field(
        default=0.08, gt=0.0, le=0.3,
        description="页眉区占页面高度的比例（自顶部算起）",
    )
    footer_zone_ratio: float = Field(
        default=0.12, gt=0.0, le=0.3,
        description="页脚区占页面高度的比例（自底部算起）",
    )
    header_footer_max_length: int = Field(
        default=60, ge=4, description="页眉页脚候选行的最大长度；正文长句不应被判为页眉页脚"
    )
    strip_cjk_spaces: bool = Field(
        default=False,
        description=(
            "是否删除中文字符之间的空格。默认关闭：官方文书里「第 一 章 总 则」这类"
            "空格是有意排版，删除会破坏原文；且中文词间本就无空格，PDF 提取插入的"
            "空格几乎都发生在跨行折行处，而折行处已经由 loader 的段落重组处理。"
            "仅当日志显示正文出现大量「中 文 字 间 空 格」时才建议开启"
        ),
    )
    extra_header_footer_patterns: list[str] = Field(
        default_factory=list, description="额外的页眉页脚正则（用户可自行补充领域相关固定文字）"
    )

    #: 清洗时保留的元数据字段名（用于检索结果的引用溯源）
    metadata_fields: list[str] = Field(
        default=[
            "source",
            "file_name",
            "page",
            "total_pages",
            "chunk_id",
            "chunk_index",
            "total_chunks",
            "strategy",
            "doc_type",
        ]
    )

    def for_strategy(self, strategy: str | None = None) -> StrategySpec:
        """按名称取出策略参数；非法名称会给出明确提示而不是静默回退。"""
        name = (strategy or self.default_strategy).strip().lower()
        if name not in ("fixed", "recursive"):
            raise ValueError(f"未知分块策略 '{strategy}'，可选：fixed | recursive")
        return getattr(self, name)

    @property
    def available_strategies(self) -> list[str]:
        return ["fixed", "recursive"]
    @property
    def chunk_size(self) -> int:
        return self.for_strategy().chunk_size

    @property
    def chunk_overlap(self) -> int:
        return self.for_strategy().chunk_overlap

    @property
    def separators(self) -> list[str]:
        return self.for_strategy().separators

    @property
    def min_chunk_size(self) -> int:
        return self.for_strategy().min_chunk_size

    @property
    def max_chunk_size(self) -> int:
        return self.for_strategy().max_chunk_size or self.for_strategy().chunk_size

    @property
    def splitter(self) -> str:
        """兼容旧字段名：返回当前默认策略名。"""
        return self.default_strategy


class PDFSettings(_Section):
    """阶段 1 使用：PDF 文本抽取（loader.py）。"""

    #: pdfplumber 对中文排版与表格抽取质量最好；PyMuPDF 更快但需额外安装
    engine: PDFEngine = Field(default="pdfplumber", description="PDF 解析后端")
    #: True 为版面流顺序；False 按内容对象顺序，双栏论文可能串行，可切换对比
    use_text_flow: bool = Field(default=False)
    #: 同行判定的垂直误差容差（磅）
    line_tolerance: float = Field(default=3.0, gt=0)
    #: 抽取失败（加密/损坏）的文件是跳过还是中断整批入库
    skip_invalid: bool = Field(default=True, description="单个 PDF 失败时跳过而非中断")
    keep_empty_pages: bool = Field(default=False, description="清洗后无内容的页是否仍保留")

    @field_validator("engine", mode="before")
    @classmethod
    def _norm_engine(cls, v: str) -> str:
        alias = {"fitz": "pymupdf", "pdf": "pdfplumber"}
        return alias.get(str(v).strip().lower(), str(v).strip().lower())


class EmbeddingSettings(_Section):
    """阶段 1 / 阶段 2 使用：BAAI/bge-large-zh-v1.5 本地向量化。"""

    provider: EmbeddingProvider = "huggingface"
    model_name: str = Field(default="BAAI/bge-large-zh-v1.5", description="HF 仓库名或本地模型目录")
    device: DeviceType = Field(default="auto", description="auto 会在有 CUDA 时自动使用 GPU")
    normalize_embeddings: bool = Field(default=True, description="BGE 系列必须归一化后算余弦相似度")
    batch_size: int = Field(default=16, ge=1, description="显存不足时调小到 4~8")
    max_length: int = Field(default=512, ge=64, le=1024, description="单条文本最大 token 数")

    #: BGE 检索指令前缀：查询侧加、文档侧不加（bge 官方用法，召回提升明显）
    query_instruction: str = "为这个句子生成表示以用于检索相关文章："
    use_query_instruction: bool = Field(default=True, description="是否给 query 加检索指令前缀")

    show_progress: bool = False
    cache_dir: Path | None = Field(default=None, description="模型缓存目录，None 则用 HF_HOME")
    offline: bool = Field(
        default=True,
        description=(
            "离线模式：只用本地缓存，不访问 HuggingFace（默认开启）。"
            "原因：sentence-transformers 每次加载都会联网校验缓存，国内直连会超时并"
            "触发 5 轮指数退避，表现为程序卡住几十分钟。"
            "关闭方式：RAG_EMBEDDING__OFFLINE=false（首次下载模型时可临时关闭）"
        ),
    )

    #: 与 bge-large-zh-v1.5 一致；换模型必须同步修改，否则 Chroma 集合会冲突
    dimension: int = Field(default=1024, ge=1)

    @field_validator("cache_dir", mode="after")
    @classmethod
    def _expand_cache(cls, v: Path | None) -> Path | None:
        return _abs(v) if v is not None else None

    @property
    def resolved_model(self) -> str:
        """若 model_name 是本地已存在的目录则返回绝对路径，否则原样返回 HF 仓库名。"""
        p = Path(self.model_name)
        if p.is_absolute() and p.exists():
            return str(p)
        candidate = PROJECT_ROOT / p
        if candidate.exists():
            return str(candidate)
        return self.model_name


class RerankSettings(_Section):
    """阶段 2 使用：BAAI/bge-reranker-v2-m3 精排。"""

    enabled: bool = Field(default=True, description="关闭后退化为纯混合检索结果")
    model_name: str = "BAAI/bge-reranker-v2-m3"
    #: 加载后端：cross_encoder -> sentence_transformers.CrossEncoder（默认，轻量）；
    #: flag_embedding -> FlagEmbedding.FlagReranker（需额外安装，可用于交叉验证）
    backend: RerankBackend = Field(default="cross_encoder", description="精排模型加载后端")
    device: DeviceType = "auto"
    #: 精排后保留条数（进入 LLM 上下文）。
    #: 完整链路为「每路召回 50 -> RRF 融合取 20 -> 精排取 5」；候选池由
    #: retrieval.top_k 控制，candidate_multiplier 仅作兜底。
    top_k: int = Field(default=5, ge=1, le=50, description="精排后保留条数（进入 LLM 上下文）")
    #: 仅当 retrieval.top_k 未显式配置时，用「检索 top_k × multiplier」推算候选池
    candidate_multiplier: int = Field(
        default=2, ge=1, le=5, description="候选池推算倍数（retrieval.top_k 优先）"
    )
    batch_size: int = Field(
        default=8, ge=1,
        description=(
            "reranker 批大小。CPU 上实测 batch=8 最快（16.0s/20候选），"
            "batch=16 反而更慢（17.8s）—— 交叉编码器是计算瓶颈而非内存瓶颈，"
            "批太大只会增加调度开销。GPU 上可调大到 16~32"
        ),
    )
    #: 精排最大序列长度。512 -> 256 实测会让 4 个查询中的 3 个 Top-5 发生变化
    #: （秩相关 1.0 -> 0.889），截断直接损害精排质量，不建议为提速调小。
    #: 本项目 chunk 中位长度 409 字，512 token 基本够用。
    max_length: int = Field(default=512, ge=64, le=1024)
    normalize_score: bool = Field(
        default=False,
        description="是否对精排分数做 sigmoid 归一化。CrossEncoder 原始输出为 logits（可为负），"
        "归一化后映射到 0~1 便于设置 score_threshold 与前端展示；"
        "排序结果不受影响，故默认关闭",
    )
    score_threshold: float = Field(default=0.0, description="低于该分数的结果丢弃（需与 normalize_score 配套理解）")
    use_fp16: bool = Field(default=True, description="GPU 上半精度推理，省显存提速")

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
    """阶段 2 使用：BM25 稀疏检索 + Chroma 稠密检索 + 融合。"""

    #: 融合后返回条数 = 精排候选池大小。
    #: 链路：每路各召回 50（dense_top_k / sparse_top_k）-> RRF 融合取本值（20）
    #: -> 精排 -> rerank.top_k（5）进入 LLM。调大可提升召回上限，
    #: 但精排耗时随之线性增长（交叉编码器逐对前向计算）。
    top_k: int = Field(default=20, ge=1, le=200, description="融合后返回条数 = 精排候选池大小")
    dense_top_k: int = Field(default=50, ge=1, le=200, description="向量检索单独召回条数")
    sparse_top_k: int = Field(default=50, ge=1, le=200, description="BM25 单独召回条数")

    #: 融合策略：dense 只用向量、sparse 只用 BM25（两者为消融基线）；
    #: rrf 按名次融合无需调权重；weighted 需自行调整 dense_weight/sparse_weight。
    fusion: FusionStrategy = Field(default="rrf", description="混合融合策略")
    dense_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    sparse_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    rrf_k: int = Field(default=60, ge=1, description="RRF 平滑常数，论文推荐 60")

    bm25_tokenizer: BM25Tokenizer = Field(default="jieba", description="中文必须用 jieba")
    bm25_k1: float = Field(default=1.5, ge=0.0, le=3.0)
    bm25_b: float = Field(default=0.75, ge=0.0, le=1.0)
    #: 查询词文档频率上限，超过则视为停用词剔除。
    #: 原因：rank_bm25 的 BM25Okapi 把负 IDF 截断成小正数(epsilon)，"的""不"这类
    #: 在 90% 文档中都出现的词反而给所有文档加分，导致查询词完全不存在时
    #: 仍返回一堆正分结果（实测 290 篇中 275 篇得正分）。
    bm25_max_df_ratio: float = Field(
        default=0.6, gt=0.0, le=1.0,
        description="查询词的文档频率上限，超过则视为停用词剔除",
    )
    #: 剔停用词后若无有效查询词，直接返回空结果而不是硬凑分数
    bm25_min_term_df: int = Field(default=1, ge=1, description="查询词至少需出现的文档数")
    #: 过滤仅由 epsilon IDF 抬高、并无真实匹配的噪声命中；
    #: 真实词匹配命中分数远高于此值（实测相关命中 14~20 分）
    bm25_score_floor: float = Field(
        default=0.5, ge=0.0, description="低于该 BM25 分数的结果丢弃"
    )

    collection_name: str = Field(default="rag_domain_qa", description="Chroma 集合名")
    deduplicate: bool = Field(default=True, description="按文本内容去重")
    #: 命中同一文档的多个相邻块时拼接成更完整上下文
    merge_adjacent: bool = False
    min_score: float = Field(default=0.0, description="融合分数下限，低于则丢弃")

    @model_validator(mode="after")
    def _check_weights(self) -> "RetrievalSettings":
        if self.fusion == "weighted" and abs(self.dense_weight + self.sparse_weight - 1.0) > 1e-6:
            raise ValueError(
                f"weighted 融合下 dense_weight+sparse_weight 必须为 1，"
                f"当前为 {self.dense_weight + self.sparse_weight}"
            )
        return self


class LLMSettings(_Section):
    """阶段 3 使用：DeepSeek OpenAI 兼容接口。"""

    provider: LLMProvider = "deepseek"
    api_key: str | None = Field(default=None, description="从 .env 的 RAG_LLM__API_KEY 读取")
    base_url: str = "https://api.deepseek.com/v1"
    model: str = Field(default="deepseek-chat", description="deepseek-chat | deepseek-reasoner")
    fallback_models: list[str] = Field(
        default=["deepseek-chat"], description="主模型调用失败时的降级候选"
    )

    temperature: float = Field(default=0.3, ge=0.0, le=2.0, description="RAG 宜低温，保证忠实度")
    top_p: float = Field(default=0.9, gt=0.0, le=1.0)
    max_tokens: int = Field(default=2048, ge=64)
    timeout: int = Field(default=120, ge=1, description="单次请求超时（秒）")
    max_retries: int = Field(default=3, ge=0, le=10)
    stream: bool = Field(default=True, description="是否默认流式输出")

    @field_validator("base_url", mode="after")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def is_configured(self) -> bool:
        """
        API Key 是否已配置（生成模块启动时用它做前置校验）。

        判据是**正面校验密钥格式**（``sk-`` + 20 位以上字母数字），而不是
        "不等于某个占位符" —— 后者防不住用户自定义的占位文字。
        本项目实测：把判据写成"不以 sk-xxx 开头"时，占位符
        ``sk-REPLACE_WITH_YOUR_KEY`` 会被误判为已配置，等到真正调用才 401。
        """
        return bool(_API_KEY_RE.match((self.api_key or "").strip()))

    @property
    def looks_like_placeholder(self) -> bool:
        """
        该值是否"形状像占位符"（``sk-`` 后为同一字符重复，如 ``sk-xxxx…``）。

        单独作为一个属性而不是并进 ``is_configured``：这类值在格式上其实合法，
        无法 100% 断定它一定是占位符（真实密钥碰巧全同字符的概率极低但不为零）。
        因此只用于**给出更有针对性的报错提示**，不参与判定。
        """
        return bool(_API_KEY_PLACEHOLDER_RE.match((self.api_key or "").strip()))

    def require_api_key(self) -> str:
        """在真正调用 LLM 前调用；未配置则抛出明确异常，而不是等 HTTP 401。"""
        raw = (self.api_key or "").strip()
        if not _API_KEY_RE.match(raw):
            reason = (
                "未填写（.env 中的 RAG_LLM__API_KEY 为空）" if not raw
                else f"当前值 {raw[:12]}… 不是有效的 DeepSeek 密钥格式"
            )
        elif self.looks_like_placeholder:
            reason = f"当前值 {raw[:12]}… 看起来仍是占位符（密钥体为同一字符重复），请换成真实密钥"
        else:
            return raw

        raise RuntimeError(
            f"DeepSeek API Key 不可用：{reason}\n"
            "请在 project/.env 中填写（不要加引号、不要保留占位符）：\n"
            "    RAG_LLM__API_KEY=sk-你的真实密钥\n"
            "密钥格式应为 sk- 后接 20 位以上字母数字。\n"
            "密钥申请: https://platform.deepseek.com/api_keys"
        )


class GenerationSettings(_Section):
    """阶段 3 使用：把检索结果拼成上下文并约束 LLM 输出。"""

    context_max_chars: int = Field(
        default=6000, ge=500, description="注入 prompt 的上下文字符上限（deepseek-chat 上下文 64K）"
    )
    include_scores: bool = Field(default=False, description="上下文里是否标注相似度分数")
    include_source: bool = Field(default=True, description="上下文里是否标注来源文件名与页码")
    citation_style: Literal["bracket", "inline", "none"] = Field(
        default="bracket", description="引用标注风格：[1] [2]"
    )
    history_max_turns: int = Field(default=3, ge=0, description="多轮对话保留的历史轮数")

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
    #: 此处不写引用标注要求：引用格式由 generation.citation_style 经 prompt.py 的
    #: _CITATION_INSTRUCTIONS 动态追加；写两遍会产生两条示例不一致的重复规则。
    user_prompt_template: str = (
        "【参考资料】\n{context}\n\n"
        "【用户问题】\n{question}\n\n"
        "请依据上述参考资料回答，并标注引用编号。"
    )
    no_context_reply: str = "抱歉，知识库中未检索到与该问题相关的内容，请尝试换一种问法或补充背景信息。"


class EvaluationSettings(_Section):
    """阶段 5 使用：检索与生成质量评估。

    测试集路径同时支持 ``data/testset.json``（阶段 5 规格）与
    ``data/eval/testset.json``（阶段 0 预置），见 ``resolve_testset_file``。
    """

    testset_file: Path = PROJECT_ROOT / "data" / "eval" / "testset.json"
    #: 备选位置（优先于 testset_file；用户按阶段 5 规格放在 data/ 下即可）
    testset_file_alt: Path = PROJECT_ROOT / "data" / "testset.json"
    result_dir: Path = PROJECT_ROOT / "data" / "eval" / "results"
    #: 图表输出目录（阶段 5 的 visualize 用）
    figure_dir: Path = PROJECT_ROOT / "data" / "eval" / "results" / "figures"

    #: 检索指标
    recall_at_k: list[int] = Field(default=[1, 3, 5, 10, 20])
    mrr_at_k: int = Field(default=10, ge=1)
    ndcg_at_k: int = Field(default=10, ge=1)
    #: 生成评估的评分维度（自建 LLM-as-Judge，替代 RAGAS）
    judge_metrics: list[str] = Field(
        default=["faithfulness", "answer_relevancy"],
        description="裁判打分维度：faithfulness（忠实度，有无编造）与 answer_relevancy（答案相关性）",
    )
    judge_model: str = "deepseek-chat"
    judge_temperature: float = Field(default=0.0, ge=0.0, le=1.0, description="裁判温度，0 保证可复现")
    judge_max_tokens: int = Field(default=400, ge=64, description="裁判输出上限（要留出 JSON 理由）")
    judge_scale: int = Field(default=5, ge=3, le=10, description="评分量程，默认 1~5 分")
    #: 并发调用裁判的线程数。裁判调用是网络等待型，适度并发可大幅缩短总耗时
    judge_workers: int = Field(default=4, ge=1, le=16)
    #: 生成评估的抽样条数上限（0 表示全部）。调试时先抽 3~5 条验证脚本正确性
    generation_sample: int = Field(default=0, ge=0)

    #: 消融实验
    ablation_modes: list[FusionStrategy] = Field(
        default=["dense", "sparse", "rrf", "weighted"],
        description="融合方式消融（对应旧字段名，保留兼容）",
    )
    #: 分块策略消融要对比的策略
    ablation_chunk_strategies: list[str] = Field(default=["fixed", "recursive"])
    #: Rerank Top-k 消融的取值
    ablation_rerank_top_k: list[int] = Field(default=[3, 5, 8])
    save_raw_records: bool = Field(default=True, description="保存逐条问答明细，便于人工复核")
    #: 评估时是否把每步耗时写进结果（答辩性能对比用）
    record_timing: bool = Field(default=True)

    @field_validator("testset_file", "testset_file_alt", "result_dir", "figure_dir", mode="after")
    @classmethod
    def _expand_eval(cls, v: Path) -> Path:
        return _abs(v)

    def resolve_testset_file(self) -> Path:
        """返回实际存在的测试集路径；都不存在时返回首选路径，由调用方提示"请先标注"。"""
        if self.testset_file_alt.exists():
            return self.testset_file_alt
        if self.testset_file.exists():
            return self.testset_file
        return self.testset_file_alt



class APISettings(_Section):
    """阶段 4 使用：FastAPI 服务。"""

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    reload: bool = False
    workers: int = Field(default=1, ge=1)
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(
        default=["http://localhost:8501", "http://127.0.0.1:8501", "http://localhost:3000"]
    )
    request_timeout: int = Field(default=180, ge=1)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    #: 启动时预热检索管线与精排模型，在后台线程执行：精排模型 2.2 GB，
    #: 阻塞启动会让服务迟迟不可用，/health 也无法报告进度。
    warmup_on_startup: bool = Field(default=True, description="启动时后台预热检索管线")
    #: 同时挂载 /api/v1 前缀路由（与不带前缀的路径并存），便于版本化调用
    mount_versioned_prefix: bool = Field(default=True, description="是否额外挂载 API 版本前缀")


class FrontendSettings(_Section):
    """阶段 4 使用：Streamlit。"""

    api_base_url: str = "http://127.0.0.1:8000"
    page_title: str = "领域知识问答系统"
    #: 用 Streamlit 的 Material 符号（字体连字）而非 emoji
    page_icon: str = ":material/menu_book:"
    layout: Literal["centered", "wide"] = "wide"
    request_timeout: int = Field(default=180, ge=1)
    show_sources: bool = Field(default=True, description="是否展示引用来源与分数")
    show_debug_panel: bool = Field(default=True, description="是否展示检索调试面板（评委演示利器）")
    max_history: int = Field(default=20, ge=1)


class LogSettings(_Section):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    dir: Path = PROJECT_ROOT / "logs"
    file_name: str = "rag_system.log"
    console: bool = True
    max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    backup_count: int = Field(default=3, ge=0)

    @field_validator("dir", mode="after")
    @classmethod
    def _expand_log(cls, v: Path) -> Path:
        return _abs(v)



class AppSettings(BaseSettings):
    """全局配置聚合对象；业务代码统一 ``from config import settings`` 使用。"""

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        env_prefix="RAG_",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
        validate_default=True,
    )

    #: 领域名称，会写入 prompt 与前端标题
    domain: str = Field(default="人工智能与检索增强生成", description="问答系统面向的领域")
    language: str = "zh"
    random_seed: int = Field(default=42, ge=0, description="保证评估实验可复现")
    debug: bool = False

    # 功能分组
    paths: PathSettings = Field(default_factory=PathSettings)
    chunk: ChunkSettings = Field(default_factory=ChunkSettings)
    pdf: PDFSettings = Field(default_factory=PDFSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    rerank: RerankSettings = Field(default_factory=RerankSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    generation: GenerationSettings = Field(default_factory=GenerationSettings)
    evaluation: EvaluationSettings = Field(default_factory=EvaluationSettings)
    api: APISettings = Field(default_factory=APISettings)
    frontend: FrontendSettings = Field(default_factory=FrontendSettings)
    log: LogSettings = Field(default_factory=LogSettings)
    @computed_field  # type: ignore[prop-decorator]
    @property
    def project_root(self) -> str:
        return str(PROJECT_ROOT)

    @property
    def rerank_candidate_k(self) -> int:
        """送入 reranker 的候选条数 = 检索 top_k × 候选倍数，并保证不小于 rerank.top_k。"""
        return max(self.retrieval.top_k * self.rerank.candidate_multiplier, self.rerank.top_k)
    def ensure_dirs(self) -> list[Path]:
        """创建所有必需的目录（幂等）。程序入口调用一次即可。"""
        created: list[Path] = []
        for d in [*self.paths.all_dirs(), self.evaluation.result_dir]:
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
                created.append(d)
        return created

    def apply_environment(self) -> None:
        """把配置回写到 os.environ，供第三方库（HF / torch / chromadb）读取。"""
        if self.embedding.cache_dir is not None:
            os.environ.setdefault("HF_HOME", str(self.embedding.cache_dir))
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        # 关闭 Chroma 匿名遥测：本项目无需上报，且当前 posthog 版本与 chromadb 0.5.x
        # 存在方法签名不兼容（capture() takes 1 positional argument but 3 were given），
        # 每次建集合/查询都会打印一行报错噪音，严重干扰日志。
        # 注意：chromadb 的 Settings 是 pydantic v1 风格，字段名为裸名 anonymized_telemetry，
        # 因此环境变量必须是 ANONYMIZED_TELEMETRY（带 CHROMA_ 前缀的写法不生效）。
        os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
        # 模型已下载后默认走离线，避免 sentence-transformers 联网校验缓存
        # 在国内网络下反复超时（表现为程序卡住几十分钟）
        if self.embedding.offline:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    def to_dict(self, hide_secrets: bool = True) -> dict[str, Any]:
        """导出为普通字典；hide_secrets=True 时脱敏 API Key（用于日志与 /config 接口）。"""
        data = self.model_dump(mode="json")
        if hide_secrets and data.get("llm", {}).get("api_key"):
            key = str(data["llm"]["api_key"])
            data["llm"]["api_key"] = f"{key[:6]}...{key[-4:]}" if len(key) > 12 else "***"
        return data

    def summary(self) -> str:
        """人类可读的配置概览，供启动日志与调试面板使用。"""
        r, rk, llm = self.retrieval, self.rerank, self.llm
        return (
            f"[配置] 领域={self.domain} | 分块={self.chunk.chunk_size}/{self.chunk.chunk_overlap} "
            f"| 混合检索 top_k={r.top_k} 融合={r.fusion} 分词={r.bm25_tokenizer} "
            f"| Rerank={'on' if rk.enabled else 'off'} top_k={rk.top_k} "
            f"| LLM={llm.model}(key={'已配置' if llm.is_configured else '未配置'}) "
            f"| device={self.embedding.device}"
        )



def _silence_chroma_telemetry_noise() -> None:
    """屏蔽 chromadb 0.5.x 的遥测报错噪音。

    chromadb 0.5.20 以三参数调用 ``posthog.capture(user_id, event, props)``，
    而 posthog 7.x 已改为 ``capture(event, **kwargs)``，必然抛 TypeError；
    chromadb 用 ``logger.error`` 打印，其 logger 无 handler，最终由
    ``logging.lastResort`` 写到 stderr —— 该兜底 handler 不支持过滤器，
    故只能直接禁用该 logger。``posthog.disabled=True`` 只禁止网络发送，拦不住签名错误。
    """
    logging.getLogger("chromadb.telemetry.product.posthog").disabled = True
    logging.getLogger("chromadb.telemetry").setLevel(logging.CRITICAL)


def configure_chroma_telemetry() -> None:
    """双重保险关闭 Chroma 遥测。

    apply_environment() 设置的环境变量只在 chromadb 的 Settings 尚未实例化时生效，
    若已被提前加载则失效，故这里再直接改写其单例配置。
    调用时机：使用 Chroma 之前（建库脚本与 API 启动时各调一次）。
    """
    _silence_chroma_telemetry_noise()
    try:
        from chromadb.config import Settings as _ChromaSettings

        _ChromaSettings().anonymized_telemetry = False
    except Exception:  # noqa: BLE001 - 遥测失败不应影响主流程
        pass


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """获取全局配置单例（带缓存，测试中可用 get_settings.cache_clear() 重置）。"""
    cfg = AppSettings()
    cfg.ensure_dirs()
    cfg.apply_environment()
    _silence_chroma_telemetry_noise()
    return cfg


#: 全局唯一配置对象，业务代码统一导入它
settings: AppSettings = get_settings()


def _configure_console_encoding() -> None:
    """把标准输出切到 UTF-8。

    Windows 控制台默认 GBK(cp936)，print 特殊符号会抛 UnicodeEncodeError，
    经管道重定向时中文会乱码；errors="replace" 保证退化为可读文本而非崩溃。
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(os.sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _main() -> None:
    """自检入口：python config.py —— 打印配置概览、目录状态与关键校验结果。"""
    _configure_console_encoding()
    cfg = AppSettings()
    created = cfg.ensure_dirs()
    cfg.apply_environment()

    print("=" * 78)
    print(" RAG 领域问答系统 —— 配置自检")
    print("=" * 78)
    print(f"PROJECT_ROOT : {PROJECT_ROOT}")
    print(f"Python       : {os.sys.version.split()[0]}")
    print(f".env 存在    : {(PROJECT_ROOT / '.env').exists()}")
    print("-" * 78)
    print(cfg.summary())
    print("-" * 78)

    print("[目录检查]")
    for d in [*cfg.paths.all_dirs(), cfg.evaluation.result_dir]:
        mark = "新建" if d in created else "已存在"
        print(f"  [{mark}] {d}")

    print("-" * 78)
    print("[关键参数]")
    for name in cfg.chunk.available_strategies:
        sp = cfg.chunk.for_strategy(name)
        mark = " <-默认" if name == cfg.chunk.default_strategy else ""
        print(f"  分块[{name:9s}] size={sp.chunk_size} overlap={sp.chunk_overlap} "
              f"min={sp.min_chunk_size} max={sp.max_chunk_size}{mark}")
    print(f"  PDF 解析引擎               : {cfg.pdf.engine} (text_flow={cfg.pdf.use_text_flow})")
    print(f"  retrieval.top_k           : {cfg.retrieval.top_k}")
    print(f"  rerank.top_k / 候选池      : {cfg.rerank.top_k} / {cfg.rerank_candidate_k}")
    print(f"  fusion                    : {cfg.retrieval.fusion} "
          f"(dense={cfg.retrieval.dense_weight}, sparse={cfg.retrieval.sparse_weight})")
    print(f"  embedding.model           : {cfg.embedding.resolved_model} (dim={cfg.embedding.dimension})")
    print(f"  rerank.model              : {cfg.rerank.resolved_model}")
    print(f"  llm.model / base_url      : {cfg.llm.model} / {cfg.llm.base_url}")
    print(f"  api / frontend            : http://{cfg.api.host}:{cfg.api.port} -> {cfg.frontend.api_base_url}")

    print("-" * 78)
    print("[LLM API Key]")
    print(f"  {'✅ 已配置' if cfg.llm.is_configured else '⚠️  未配置（阶段 3 前请填写 .env 的 RAG_LLM__API_KEY）'}")

    print("-" * 78)
    print("[完整配置 JSON]")
    print(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2))
    print("=" * 78)


if __name__ == "__main__":
    _main()
