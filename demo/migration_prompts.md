# 领域知识库系统迁移提示词全集

本文档用于将**参考项目 `demo/`**（基于 **Streamlit + FastAPI** 的 RAG 问答系统），迁移为基于 **FastAPI + Vue3 + MySQL + Redis + MinIO + Celery** 的产品级领域知识库系统。

**第一条硬约束：`demo/` 目录必须原样保留、只读。**
动手前务必先读下一节「迁移边界与目录约定」。

---

## 迁移边界与目录约定（先读）

本项目的迁移方式为**旁路新建**，不是原地改造，务必遵守以下边界：

1. **`demo/` 是只读参考项目**：不得修改、删除、移动或重命名 `demo/` 下的任何文件 —— 包括代码、`.env`、`.gitignore`、`indexes/`、`data/`（原始 PDF、分块 JSON、评估结果）、`README.md`。`demo/` 必须始终保持可独立运行。
2. **新项目落点**：在**仓库根**新增独立目录 `product/`，其下再分 `backend/`（FastAPI）与 `frontend/`（Vue3）。迁移产物全部放进 `product/` 内，与 `demo/` 零耦合。
3. **"复用 / 迁移现有模块" = 复制**：文档中所有"复用现有 `src/xxx/`""迁移现有模块"均指**从 `demo/src/xxx/` 复制到 `product/backend/src/xxx/` 后再改**，绝不回到 `demo/` 原地修改。
4. **独立依赖环境**：`product/backend` 自建虚拟环境（如 `product/backend/.venv`），**不复用也不污染** `demo/.venv`；两边各自独立的 `requirements.txt`。
5. **独立配置与数据**：`product/backend` 自带 `.env`、`data/`、`indexes/`、`storage/`、`logs/`，路径基准为**新项目根**；不得与 `demo/indexes/`、`demo/data/` 共用同一目录。
6. **旧索引与评估产物不搬迁**：`demo/indexes/`（Chroma、BM25 pickle）与 `demo/data/processed/` 是那套单语料的产物，**不要**搬到新项目充当多知识库索引，新项目按 `kb_{kb_id}` 重建。评估数据与结果**全部留在 `demo/`**（新项目不做评估，见 4.8），需要时在 demo 中查看，不复制、不移动。
7. **每阶段自检**：每完成一步运行 `git status`，结果只应出现 `product/` 下的**新增**文件，`demo/` 下不应有任何 modified / deleted。

---

## 0. 项目背景与迁移目标

### 0.1 参考项目 demo/ 现状（已按实际代码校正）

- **前端**：Streamlit 单文件 `app.py`（只做展示，不参与任何计算）。
- **后端**：`src/api/` 分三层——`schemas.py`（对外契约）→ `service.py`（业务编排，含**后台线程预热**）→ `main.py`（路由），另有 `ingest.py`（增量入库模块）。现有路由：`/health`、`/warmup`、`/query`、`/query/stream`(SSE)、`/retrieve`、`/strategies`、`/config`(密钥脱敏)、`/ingest`、`/ingest/files/{name}`、`/ingest/status/{task_id}`、`/ingest/tasks`、`/ingest/manifest`。
- **检索**：`src/retrieval/`——`vector_retriever`（BGE 稠密）、`bm25_retriever`（BM25Okapi + jieba）、`hybrid_retriever`（RRF，k=60）、`reranker`（CrossEncoder）、`pipeline`（编排）。
- **生成**：`src/generation/`——`prompt`（PromptBuilder + 上下文预算裁剪）、`llm`（LLMClient + 两层重试）、`citation`（引用宽松解析 + 严格映射）。
- **评估**：`src/evaluation/`——`testset` / `metrics` / `retrieval_eval` / `judge`（**自建 LLM-as-Judge**）/ `generation_eval` / `ablation` / `visualize`。**明确不使用 RAGAS**。
- **配置**：单文件 `config.py`，11 个分组（路径 / 分块 / 嵌入 / 检索 / 精排 / 大模型 / 生成 / 接口 / 前端 / 评估 / 日志），支持环境变量覆盖，命名规则 `RAG_<分组>__<字段>`（如 `RAG_LLM__API_KEY`、`RAG_EMBEDDING__OFFLINE`）。
- **数据**：本地 PDF；Chroma 本地持久化（HNSW，集合 `rag_domain_qa_fixed` / `rag_domain_qa_recursive`）；`rank_bm25` 本地 pickle；`data/processed/` 存分块 JSON 与 `ingest_manifest.json`（sha256 + 块数）。
- **默认面向 CPU**（无 GPU），精排是性能瓶颈。
- **无**数据库、无用户系统、无多知识库、无会话持久化、无异步任务队列。

> 关键链路参数（迁移中不得改动）：每路召回 50 → RRF(k=60) 融合 → 候选池 20 → 精排保留 **5** → 上下文预算 **6000 字**。
> 分块参数：`fixed`(chunk_size=500, overlap=50, max=800) / `recursive`(chunk_size=500, overlap=50, separators=["\n\n","\n","。"," ",""], max=1200)。

### 0.2 迁移目标

构建一个多知识库、多轮对话、可观测的领域知识库系统，具备：

- 无登录、无用户体系，单机开箱即用
- 预置 1 个知识库「法律领域」，同时支持随时新增知识库
- 知识库 CRUD，文档上传（pdf / docx / txt / md 四种格式）、解析、索引、删除
- 多知识库隔离（每个知识库独立的向量索引和 BM25 索引）
- 检索策略按构建块开关自由组合（向量 / BM25 / 融合 RRF / 精排 Rerank）
- 多轮对话，会话管理，流式输出，引用溯源
- 结构化日志（检索日志 + 系统日志）、指标
- Docker Compose 一键部署

### 0.3 目标技术栈

| 层级 | 技术选型 |
| --- | --- |
| 前端 | Vue3 + Vite + Element Plus + Pinia + Vue Router + Axios |
| 后端 | FastAPI + SQLAlchemy 2.0 (async) + Alembic + Pydantic v2（锁 2.9.x，见 0.4） |
| 数据库 | MySQL 8.0（utf8mb4） |
| 缓存/队列 | Redis + Celery |
| 文件存储 | MinIO（或本地存储，毕设可先用本地） |
| 向量库 | Chroma（毕设）/ Milvus（产品） |
| 稀疏检索 | rank_bm25（毕设）/ Elasticsearch（产品） |
| RAG 框架 | LangChain 0.2.17（**仅用于文档加载/切分，链路自研**） |
| Embedding | `BAAI/bge-large-zh-v1.5`（1024 维，查询侧加官方指令前缀） |
| Rerank | `BAAI/bge-reranker-v2-m3`（经 `sentence-transformers` 的 `CrossEncoder` 加载，**不装 FlagEmbedding**） |
| LLM | DeepSeek API（OpenAI 兼容） |
| 可观测 | loguru + Prometheus + OpenTelemetry（可选） |
| 部署 | Docker Compose |

### 0.4 版本与兼容性约束（迁移时务必沿用）

| 约束 | 原因 |
| --- | --- |
| Python 3.11.9 | 依赖兼容性已实测 |
| `pydantic >=2.9,<2.10` | langchain-core 0.2.x 内部仍用 pydantic v1 的 `class Config` 写法，2.10 已移除会抛错 |
| `chromadb >=0.5.5,<0.6.0` | 支持 chromadb 1.x 的 `langchain-chroma>=0.2.4` 要求 `langchain-core>=0.3.60`，与 LangChain 0.2.x 硬冲突 |
| **不安装 `FlagEmbedding`** | reranker 已支持用 `sentence-transformers` 的 `CrossEncoder` 加载，省下约 1.5 GB 传递依赖 |
| `langchain 0.2.17` | 只取文档加载/切分能力，检索与生成逻辑自研，便于控制与调试 |

---

## 1. 目标架构

```
┌─────────────────────────────────────────────────────────┐
│  前端 Vue3 + Element Plus（4 页）                               │
│  ├─ 问答页（多轮/引用/检索可视化）                                            │
│  ├─ 知识库页（列表/创建/上传/文档管理）                     │
│  ├─ 检索策略页（构建块开关：向量/BM25/融合/精排）                      │
│  └─ 日志页（检索日志 + 系统日志）                        │
└─────────────────────────────────────────────────────────┘
                          ↓ RESTful API
┌─────────────────────────────────────────────────────────┐
│  后端 FastAPI（无认证）                                           │
│  ├─ 文档入库（pdf/docx/txt/md 解析）                               │
│  ├─ 知识库服务（CRUD + 文档管理）                         │
│  ├─ 检索服务（按 enabled_strategies 组装 pipeline）                    │
│  ├─ 对话服务（多轮 + 会话管理 + 流式）                    │
│  └─ 可观测（日志/指标/追踪）                              │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│  数据层                                                  │
│  ├─ MySQL：知识库/文档/会话/消息/检索日志                     │
│  ├─ Redis：会话缓存/限流/Celery 队列                      │
│  ├─ Chroma/Milvus：向量索引（按知识库隔离）               │
│  ├─ rank_bm25/Elasticsearch：BM25 索引                   │
│  └─ MinIO/本地：原始文档存储                              │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│  AI 层                                                   │
│  ├─ LangChain：仅文档加载/切分                            │
│  ├─ Embedding：bge-large-zh-v1.5                         │
│  ├─ Rerank：bge-reranker-v2-m3                           │
│  └─ LLM：DeepSeek API                                    │
└─────────────────────────────────────────────────────────┘
```

---

## 2. 技术栈与目录结构

### 2.1 后端目录结构（目标）

```
backend/
├── alembic/                     # 数据库迁移
│   ├── versions/
│   └── env.py
├── src/
│   ├── api/
│   │   ├── v1/
│   │   │   ├── kb.py            # 知识库管理（含 /kb/{id}/strategies）
│   │   │   ├── document.py      # 文档管理
│   │   │   ├── chat.py          # 对话（含 /chat/stream）
│   │   │   ├── retrieval.py     # 检索调试
│   │   │   └── log.py           # 检索日志 / 系统日志查询
│   │   ├── deps.py              # 依赖注入
│   │   └── main.py              # FastAPI 入口
│   ├── core/
│   │   ├── config.py            # 配置（沿用 11 分组 + RAG_<分组>__<字段> 覆盖）
│   │   └── logging.py           # 日志
│   ├── db/
│   │   ├── base.py              # Base 和 engine
│   │   ├── session.py           # session 依赖
│   │   └── models/              # ORM 模型
│   │       ├── knowledge_base.py
│   │       ├── document.py
│   │       ├── conversation.py
│   │       ├── message.py
│   │       └── retrieval_log.py
│   ├── schemas/                 # Pydantic 模型
│   │   ├── kb.py
│   │   ├── document.py
│   │   └── chat.py
│   ├── services/                # 业务逻辑
│   │   ├── kb_service.py
│   │   ├── document_service.py
│   │   ├── retrieval_service.py
│   │   └── chat_service.py
│   ├── retrieval/               # 从 demo/src/retrieval/ 复制（只加 kb_id 适配）
│   │   ├── vector_retriever.py
│   │   ├── bm25_retriever.py
│   │   ├── hybrid_retriever.py
│   │   ├── reranker.py
│   │   └── pipeline.py
│   ├── generation/              # 从 demo/src/generation/ 复制
│   │   ├── prompt.py
│   │   ├── llm.py
│   │   └── citation.py
│   ├── ingest/                  # 文档解析与索引
│   │   ├── loader.py            # 按扩展名分派：pdf/docx/txt/md
│   │   ├── parsers/             # 各格式解析器
│   │   │   ├── pdf_parser.py
│   │   │   ├── docx_parser.py
│   │   │   └── text_parser.py   # txt 与 md
│   │   ├── splitter.py
│   │   └── indexer.py
│   ├── tasks/                   # Celery 任务
│   │   ├── celery_app.py
│   │   └── document_tasks.py
│   └── utils/
│       ├── minio_client.py
│       └── redis_client.py
├── tests/
├── scripts/
│   ├── init_db.py
│   └── seed_data.py             # 预置「法律领域」知识库并导入 resource/法律领域/
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

### 2.2 前端目录结构（目标）

```
frontend/
├── public/
├── src/
│   ├── api/                     # Axios 封装
│   │   ├── request.js
│   │   ├── kb.js
│   │   ├── document.js
│   │   ├── chat.js
│   │   └── log.js
│   ├── assets/
│   ├── components/
│   │   ├── ChatMessage.vue
│   │   ├── CitationCard.vue
│   │   ├── RetrievalDetail.vue
│   │   └── StrategyBuilder.vue  # 构建块开关：向量/BM25/融合/精排
│   ├── layouts/
│   │   └── MainLayout.vue
│   ├── router/
│   │   └── index.js
│   ├── stores/
│   │   ├── kb.js
│   │   └── chat.js
│   ├── views/
│   │   ├── ChatQuery.vue        # 问答页
│   │   ├── KnowledgeBase.vue    # 知识库页（列表/创建/文档管理）
│   │   ├── RetrievalStrategy.vue# 检索策略页
│   │   └── Logs.vue             # 日志页（检索日志 + 系统日志）
│   ├── App.vue
│   └── main.js
├── package.json
├── vite.config.js
└── index.html
```

---

## 3. 数据库设计（MySQL）

### 3.1 完整 DDL

```sql
CREATE DATABASE IF NOT EXISTS rag_system CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE rag_system;

-- 知识库表（无用户体系；enabled_strategies 存 4 个构建块开关）
CREATE TABLE knowledge_bases (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    chunk_strategy VARCHAR(50) DEFAULT 'recursive',
    enabled_strategies JSON NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 文档表
CREATE TABLE documents (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    kb_id BIGINT NOT NULL,
    filename VARCHAR(255) NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    file_size BIGINT,
    file_type ENUM('pdf','docx','txt','md') NOT NULL,
    status ENUM('pending', 'parsing', 'indexing', 'completed', 'failed') DEFAULT 'pending',
    chunk_count INT DEFAULT 0,
    error_msg TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (kb_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    INDEX idx_kb_status (kb_id, status)
);

-- 会话表
CREATE TABLE conversations (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    kb_id BIGINT NOT NULL,
    title VARCHAR(200),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (kb_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    INDEX idx_kb (kb_id)
);

-- 消息表
CREATE TABLE messages (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    conversation_id BIGINT NOT NULL,
    role ENUM('user', 'assistant') NOT NULL,
    content TEXT NOT NULL,
    citations JSON,
    retrieved_docs JSON,
    latency JSON,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    INDEX idx_conversation (conversation_id)
);

-- 检索日志表
CREATE TABLE retrieval_logs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    kb_id BIGINT,
    query TEXT NOT NULL,
    strategy VARCHAR(50),
    top_k INT,
    latency FLOAT,
    result_count INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_kb_created (kb_id, created_at)
);

-- 不做用户体系：无 users / kb_permissions 表；不做评估：无 evaluations 表（见 4.8）。
```

---

## 4. 后端模块设计与提示词

### 4.1 项目初始化与依赖

给 AI 的提示词：

```
请在 product/backend/ 下初始化一个新的 FastAPI 后端项目，目录结构见附件。技术栈与版本必须与参考项目 demo/ 对齐（demo/ 只读，不得改动）：
- Python 3.11.9
- FastAPI + Uvicorn
- SQLAlchemy 2.0 (async) + aiomysql
- Alembic
- pydantic >=2.9,<2.10（不要用 2.10+，会与 langchain-core 0.2.x 冲突）
- celery + redis
- minio
- langchain 0.2.17（只用于文档加载/切分）
- chromadb >=0.5.5,<0.6.0
- rank_bm25 + jieba
- sentence-transformers
- pdfplumber（pdf 解析）
- python-docx（docx 解析）
- loguru

注意：
1. 不要安装 FlagEmbedding（reranker 用 sentence-transformers 的 CrossEncoder 加载即可）。
2. 不要引入 python-jose / passlib（新项目无登录），也不要引入 RAGAS / datasets（新项目不做评估，见 4.8）。
3. 文档上传支持 pdf / docx / txt / md 四种格式，解析链路见 4.3。
请生成 requirements.txt，并创建基础目录结构。
```

### 4.2 数据库层

给 AI 的提示词：

```
请实现数据库层：
1. src/db/base.py：
   - 创建 AsyncEngine，连接字符串从环境变量 DATABASE_URL 读取
   - 连接池：pool_size=10, max_overflow=20, pool_pre_ping=True
   - 定义 Base = declarative_base()
2. src/db/session.py：
   - 创建 async_sessionmaker
   - 提供 get_db() 依赖，yield AsyncSession
3. src/db/models/ 下按表创建 ORM 模型：
   - knowledge_base.py, document.py, conversation.py, message.py, retrieval_log.py
   - 使用 SQLAlchemy 2.0 风格（Mapped, mapped_column）
   - JSON 字段用 JSON 类型（knowledge_bases.enabled_strategies、messages.citations/retrieved_docs/latency）
   - 时间字段用 server_default=func.now()
4. alembic 配置：
   - alembic init alembic
   - 修改 env.py 支持 async 和 Base.metadata
   - 生成初始迁移脚本
请生成完整代码。
```

### 4.3 文档解析与入库模块（多格式 loader）

给 AI 的提示词：

```
请从 demo/src/ingest/ 复制 loader/splitter/indexer 到 product/backend/src/ingest/（不改 demo/ 原件），
并把 loader 改造为**按扩展名分派**，支持 pdf / docx / txt / md 四种格式，统一产出同一 Page/Chunk 结构：

1. src/ingest/loader.py：
   - 按文件扩展名分派到 src/ingest/parsers/ 下对应解析器
   - 所有解析器输出统一结构：每页/每段含 text、source（文件名）、page（页码/段号）
2. src/ingest/parsers/：
   - pdf_parser.py：沿用 demo 的 pdfplumber 链路（逐页抽取文本）
   - docx_parser.py：用 python-docx 逐段落抽取文本，段落序号作为 page
   - text_parser.py：txt 与 md 直接读取（md 保留原文标记，不剥离），整体作为一页
3. src/ingest/splitter.py：沿用 demo 分块（fixed / recursive 两套参数，见 0.1）
4. src/ingest/indexer.py：沿用 demo 的 chunk_id 生成规则（文件名 + 页码 + 块序号 + 正文哈希）与双写
   （Chroma 向量 + BM25 pickle），只加 kb_id 适配
请生成完整代码。

注意：四种格式必须收敛到同一 Chunk 结构，后续切分 / 索引 / 引用溯源对格式无感知。
```

### 4.4 知识库管理模块

给 AI 的提示词：

```
请实现知识库管理模块（无用户体系，不涉及权限）：
1. src/schemas/kb.py：
   - KBCreate: name, description, chunk_strategy, enabled_strategies(可选)
   - KBUpdate: 可选字段（含 enabled_strategies）
   - KBOut: id, name, description, chunk_strategy, enabled_strategies, created_at
2. src/services/kb_service.py：
   - create_kb: 新建知识库，enabled_strategies 默认全开（{"vector":true,"bm25":true,"fusion":true,"rerank":true}）
   - list_kbs: 返回全部知识库
   - get_kb
   - update_kb: 含更新 enabled_strategies（构建块开关）
   - delete_kb: 同时删除关联的向量索引、BM25 索引、文档
3. src/api/v1/kb.py：
   - GET /kb
   - POST /kb
   - GET /kb/{kb_id}
   - PUT /kb/{kb_id}
   - PATCH /kb/{kb_id}/strategies：单独更新 enabled_strategies
   - DELETE /kb/{kb_id}
请生成完整代码。

多知识库隔离要求：
- Chroma collection_name = f"kb_{kb_id}"，持久化目录 indexes/chroma/kb_{kb_id}/
- BM25 索引文件 indexes/bm25/kb_{kb_id}.pkl
- 保留 demo 的 chunk_id 生成规则（文件名 + 页码 + 块序号 + 正文哈希，见 demo/src/ingest/indexer.py），确保同一 kb 内同样输入必然得到同样 id
- 每个 kb 维护独立的 ingest_manifest.json（sha256 + 块数），用于增量入库
```

### 4.5 文档管理模块

给 AI 的提示词：

```
请实现文档管理模块：
1. src/schemas/document.py：
   - DocumentOut: id, kb_id, filename, file_type, file_size, status, chunk_count, error_msg, created_at
2. src/services/document_service.py：
   - upload_document: 校验扩展名仅允许 pdf / docx / txt / md，其余类型直接拒绝；
     文件保存到 MinIO 或本地 storage/{kb_id}/，创建文档记录（file_type 按扩展名写入），状态 pending
   - list_documents: 按 kb_id 查询
   - delete_document: 删除文件、数据库记录、对应的向量和 BM25 索引
   - get_document: 详情
3. src/api/v1/document.py：
   - POST /kb/{kb_id}/documents (UploadFile)
   - GET /kb/{kb_id}/documents
   - GET /documents/{doc_id}
   - DELETE /documents/{doc_id}
4. 异步任务触发：
   - 上传成功后，调用 Celery 任务 parse_and_index_document.delay(doc_id)
   - 文档状态更新：pending -> parsing -> indexing -> completed / failed
请生成完整代码。文件存储先用本地 storage/ 目录，预留 MinIO 接口。

入库必须保留 demo 的两道防线：
1. 全部文件处理完后一次性写入分块文件（不要在逐文件循环里写，否则后一个文件会覆盖前一个）。
2. 入库结束做一致性校验：manifest 记录的块数之和 == 索引实际条数，不一致则报错中止。
```

### 4.6 检索服务模块（复制与适配）

给 AI 的提示词：

```
请将 demo/src/retrieval/ 下的模块**复制**到 product/backend/src/retrieval/，并适配多知识库（不要改动 demo/ 原件）。只做"加 kb_id 参数"的适配，不要改动检索算法本身：

1. 保留原有检索器：vector_retriever.py, bm25_retriever.py, hybrid_retriever.py, reranker.py, pipeline.py
   - 修改为接受 kb_id，从对应 Chroma collection 和 BM25 索引文件加载

2. 索引存储路径（均相对 **product/backend 根**，不要指向 demo/indexes/）：
   - 向量库：indexes/chroma/kb_{kb_id}/
   - BM25：indexes/bm25/kb_{kb_id}.pkl

3. 必须原样保留的链路参数（不要"顺手优化"）：
   - 双路召回各 Top-50
   - RRF 融合 k=60（只用名次，不用分数）
   - 融合后候选池 20
   - 精排保留 Top-5
   - 上下文预算 6000 字
   - 查询侧给 BGE 加官方检索指令前缀（"为这个句子生成表示以用于检索相关文章："）

4. src/services/retrieval_service.py：
   - retrieve(kb_id, query, top_k) -> List[Document]
   - 按知识库的 enabled_strategies（构建块开关）组装链路，不按固定策略名分派：
     * vector / bm25 决定是否启用对应召回
     * fusion 决定双路结果是否做 RRF 融合（仅开单路召回时 fusion 无意义）
     * rerank 决定候选是否过精排模型
   - 开关组合须能还原 demo 的四种策略：只开 vector → 纯向量；只开 bm25 → 纯 BM25；
     vector+bm25+fusion → 混合；全开 → 混合+Rerank
   - 对非法组合（未开融合却同时开两路召回）默认按融合执行，并记录实际生效策略
   - 记录检索日志到 retrieval_logs 表（strategy 字段记录本次实际生效的策略名）

5. src/api/v1/retrieval.py：
   - POST /kb/{kb_id}/retrieve：调试用，返回检索结果和耗时（对应 demo 的 /retrieve 与 /strategies）

6. 并发与阻塞：
   - 检索是阻塞的 CPU 操作。在 async 应用中必须通过 run_in_threadpool（或保持普通 def 端点）执行，
     不要写成 async def 再直接调用同步检索代码，否则会阻塞事件循环、导致并发请求排队。
请生成完整代码，确保多知识库隔离。
```

### 4.7 对话服务模块

给 AI 的提示词：

```
请实现对话服务模块，支持多轮对话和流式输出：
1. src/schemas/chat.py：
   - ChatRequest: kb_id, conversation_id (可选), question, strategy (可选)
   - ChatResponse: answer, citations, retrieved_docs, latency, conversation_id
   - ConversationOut: id, title, created_at
   - MessageOut: id, role, content, citations, created_at
2. src/services/chat_service.py：
   - create_conversation / get_conversation / list_conversations（按 kb_id 过滤）/ delete_conversation
   - chat: 核心逻辑
     * 如果无 conversation_id，创建新会话
     * 保存用户消息
     * 调用 retrieval_service.retrieve
     * 调用 generation.llm.generate
     * 保存助手消息（含 citations, retrieved_docs, latency）
     * 返回 ChatResponse
3. 生成侧必须复用 demo/src/generation/ 模块（**复制**到 product/backend/src/generation/，不改 demo 原件），不得重写：
   - prompt.py：上下文预算裁剪（按相关性降序累加至超预算即停；单条超预算一半时截断而非丢弃）
   - llm.py：两层重试（SDK 负责连接错误/429/5xx，应用层只重试超时并检查剩余预算）；
            调用方显式指定 model 时不降级
   - citation.py：宽松解析（兼容 [1][2]、[1,2]、[1、2]、[1-3]、【1】、（1））
                + 严格映射回上下文编号
                + 区分 unused_contexts（送入未引用）与 invalid_indices（引用不存在的编号=幻觉信号）
   - 生成参数 temperature=0.3；引用格式要求作为独立指令片段动态追加，不要在系统提示词里重复
4. 流式输出：
   - 实现 async generator，逐 token yield
   - 使用 FastAPI StreamingResponse，media_type="text/event-stream"（对应 demo 的 /query/stream）
   - 前端用 EventSource 接收
   - 注意：流式时先累积完整文本，再统一做引用解析，避免分片把 [1] 切断导致映射失败
5. src/api/v1/chat.py：
   - POST /chat（普通）
   - POST /chat/stream（流式）
   - GET /conversations
   - GET /conversations/{id}/messages
   - DELETE /conversations/{id}
请生成完整代码。
多轮对话时，可先用 LLM 做 query 改写（把"它有什么特点"改写为完整问题）再检索；改写不得改变引用编号与最终溯源映射的一致性。
```

### 4.8 评估（不在新项目实现）

> **定位**：新项目**不做评估模块**。评估已在 `demo/` 中完成（四种检索策略的 100 题对比 + 两组消融 + 生成质量，产物在 `demo/data/eval/results/`），结论直接作为实验证据；两个项目同处一个 git 仓库，需要时直接查看 `demo/` 即可。
>
> 因此新项目**不需要**：评估相关的 ORM 模型 / 数据表、service、Celery 任务、FastAPI 路由、前端评估页面。线上链路（检索 / 对话）不受影响。

（本节无代码提示词。）

### 4.9 可观测模块

给 AI 的提示词：

```
请实现可观测模块：
1. src/core/logging.py：
   - 用 loguru 配置结构化日志
   - 输出到控制台和文件 logs/app.log
   - 记录请求 ID、耗时
2. 中间件：
   - 在 FastAPI 中加中间件，记录每个请求的方法、路径、状态码、耗时
   - 注入 request_id
3. 指标（可选）：
   - 用 prometheus-client 暴露 /metrics
   - 记录 QPS、延迟直方图、错误计数
4. src/api/v1/log.py（供前端日志页）：
   - GET /logs/retrieval：分页查询 retrieval_logs（支持按 kb_id 过滤）
   - GET /logs/system：读取 logs/app.log 尾部 N 行（支持条数与级别/关键字过滤）
请生成完整代码。
```

### 4.10 Celery 异步任务

给 AI 的提示词：

```
请配置 Celery：
1. src/tasks/celery_app.py：
   - Celery 实例，broker 和 backend 都用 Redis
   - 从环境变量读取 REDIS_URL
   - 自动发现任务
2. src/tasks/document_tasks.py：
   - parse_and_index_document(doc_id):
     * 更新状态 parsing
     * 调用 ingest.loader 解析文档
     * 调用 ingest.splitter 分块
     * 调用 ingest.indexer 构建向量和 BM25 索引
     * 入库完成后做一致性校验（manifest 块数之和 == 索引条数）
     * 更新状态 completed、chunk_count
     * 异常时更新状态 failed 并记录 error_msg
3. 启动命令：
   - celery -A src.tasks.celery_app worker --loglevel=info
请生成完整代码。
注意：Celery 任务参数只用基本类型（如 doc_id），不要传 ORM 对象。
```

### 4.11 FastAPI 主入口与路由

给 AI 的提示词：

```
请实现 FastAPI 主入口 src/api/main.py：
1. 创建 FastAPI 实例，title="基于混合检索与Rerank的领域知识库系统"
2. 加 CORS 中间件
3. 加请求日志中间件
4. 注册路由（无认证中间件，不加任何鉴权、不注册 /api/v1/auth）：
   - /api/v1/kb
   - /api/v1/documents
   - /api/v1/retrieval
   - /api/v1/chat
   - /api/v1/logs
5. 加 /health 与 /metrics；/health 需返回 warmed_up 字段
6. 模型加载：Embedding 与 Rerank 模型必须放在**后台线程**预热，不要在 lifespan 里同步等待——
   精排模型 2.2 GB 加载需十几秒，同步等待会导致这段时间服务完全不响应（连 /health 都不通）。
   目标状态：服务启动即可响应，预热期间 /health 返回 warmed_up=false，提供 /warmup 手动触发（幂等）。
7. 关闭时清理资源
请生成完整代码。
```

### 4.12 配置管理

给 AI 的提示词：

```
请实现 src/core/config.py，用 pydantic-settings，并沿用 demo/config.py 的配置结构（demo/ 只读）：
- 按功能分为 10 个分组：paths, chunking, embedding, retrieval, rerank, llm, generation, api, frontend, logging
- 支持环境变量覆盖，命名规则 RAG_<分组>__<字段>（如 RAG_LLM__API_KEY、RAG_EMBEDDING__OFFLINE）
- 提供打印全部生效配置的能力（密钥脱敏，对应 demo 的 /config 接口）

字段至少包括：
- DATABASE_URL（MySQL 连接串）
- REDIS_URL（Redis 连接串）
- DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL（https://api.deepseek.com）
- EMBEDDING_MODEL_PATH: BAAI/bge-large-zh-v1.5（含查询指令前缀配置）
- RERANKER_MODEL_PATH: BAAI/bge-reranker-v2-m3
- MINIO_ENDPOINT / MINIO_ACCESS_KEY / MINIO_SECRET_KEY / MINIO_BUCKET
- STORAGE_TYPE: local / minio
- RESOURCE_DIR: 仓库根 resource/ 路径（种子数据只读引用，见 4.13）
- CHROMA_PERSIST_DIR: indexes/chroma（相对 **product/backend 根**，不得指向 demo/indexes）
- BM25_INDEX_DIR: indexes/bm25（相对 **product/backend 根**，不得指向 demo/indexes）
- 检索链路参数：recall_top_k=50, fusion_top_k=20, rerank_top_k=5, rrf_k=60, context_max_chars=6000
所有配置从 .env 读取（无登录，不需要 SECRET_KEY / JWT 相关配置）。
```

### 4.13 初始化种子数据

给 AI 的提示词：

```
请实现 scripts/seed_data.py，用于一键初始化预置知识库：

1. 预置 1 个知识库「法律领域」，enabled_strategies 默认全开
2. 扫描仓库根 resource/法律领域/ 下所有 pdf / docx / txt / md 文件（**只读引用，不复制、不改写**），
   该目录当前含 txt / docx / md / pdf 四种格式的法律文本，四种格式都会被解析入库
3. 复用 4.3 的 loader 按扩展名解析，走 4.6 的索引链路，写入该 kb 的 Chroma 与 BM25 索引，
   并在 documents 表登记对应记录（file_type 按扩展名写入，状态 completed）
4. 脚本幂等：已存在同名知识库或同名文档时跳过，不重复建

另有 scripts/init_db.py 负责建库建表（或执行 Alembic 迁移）。
请生成完整代码。

注意：resource/ 是仓库既有只读资源，导入过程只读取；若同一部法律同时存在多种格式副本，
只保留一份，避免重复入库。
```

---

## 5. 前端 Vue3 迁移提示词

### 5.1 项目初始化

给 AI 的提示词：

```
请用 Vite 创建 Vue3 项目，技术栈：
- Vue3 + Composition API
- Element Plus
- Pinia
- Vue Router
- Axios
共四个页面：问答 / 知识库 / 检索策略 / 日志（无登录页、无设置页、无评估页）。
目录结构见附件。配置代理：/api -> http://localhost:8000
```

### 5.2 问答页（ChatQuery.vue）

给 AI 的提示词：

```
请实现问答页 ChatQuery.vue：
1. 顶部：知识库选择下拉框（默认选中预置的「法律领域」）
2. 左侧：会话列表，可新建、切换、删除
3. 右侧：对话区域
   - 消息气泡：用户右对齐，AI 左对齐
   - AI 回答支持 Markdown 渲染
   - 引用编号 [1][2] 可点击，滚动到引用卡片
   - 引用卡片：展示来源文件、页码、原文
   - 检索详情：可折叠，展示 Top-5 文档块与两路召回得分
4. 底部：输入框；只读展示当前知识库生效的检索策略（修改请前往「检索策略」页）
5. 流式输出：用 EventSource 接收 POST /api/v1/chat/stream
6. 调用接口：
   - POST /api/v1/chat/stream
   - GET /api/v1/conversations?kb_id=
   - GET /api/v1/conversations/{id}/messages
```

### 5.3 知识库页（KnowledgeBase.vue）

给 AI 的提示词：

```
请实现知识库页 KnowledgeBase.vue：
1. 知识库列表：表格展示 名称、描述、分块策略、创建时间
   - 按钮：新建知识库、进入详情、删除
   - 新建对话框：名称、描述、分块策略（fixed / recursive）
2. 知识库详情：文档管理
   - 上传：el-upload，限定 .pdf/.docx/.txt/.md，调用 POST /api/v1/kb/{kb_id}/documents
   - 列表：表格展示 filename, file_type, size, status, chunk_count, created_at
   - 状态轮询：上传后每 3 秒刷新列表，直到 completed 或 failed
   - 删除：确认后调用 DELETE /api/v1/documents/{doc_id}
3. 支持随时新增知识库（空知识库亦可先创建，之后再上传文档）
```

### 5.4 检索策略页（RetrievalStrategy.vue）

给 AI 的提示词：

```
请实现检索策略页 RetrievalStrategy.vue：
1. 顶部：选择知识库
2. 核心：4 个**互不重叠的构建块开关**（el-switch），分别对应：
   - 向量召回（vector）
   - BM25 召回（bm25）
   - RRF 融合（fusion，k=60）
   - 精排 Rerank（rerank）
   开关状态对应 knowledge_bases.enabled_strategies，
   通过 PATCH /api/v1/kb/{kb_id}/strategies 保存
3. 组合说明（只读展示，帮助理解）：
   - 向量 only → 纯向量
   - BM25 only → 纯 BM25
   - 向量 + BM25 + 融合 → 混合
   - 全开 → 混合 + Rerank
4. 参数只读展示：双路召回 Top-50、RRF k=60、候选池 20、精排 Top-5、上下文预算 6000 字
5. 保存成功给出提示
```

### 5.5 日志页（Logs.vue）

给 AI 的提示词：

```
请实现日志页 Logs.vue，两个标签页：
1. 检索日志：调用 GET /api/v1/logs/retrieval，表格展示 kb、query、生效策略、top_k、耗时、结果数、时间；
   支持按 kb 过滤与分页
2. 系统日志：调用 GET /api/v1/logs/system，展示 logs/app.log 尾部内容（等宽字体、可滚动），支持选择条数
```

### 5.6 API 封装与状态管理

给 AI 的提示词：

```
请实现前端 API 封装和 Pinia store：
1. src/api/request.js：Axios 实例，baseURL='/api/v1'，统一错误提示（无鉴权拦截器）
2. src/api/kb.js, document.js, chat.js, log.js
3. src/stores/kb.js：知识库列表、当前知识库
4. src/stores/chat.js：会话列表、当前会话、消息列表
（无 user store，无登录 / 鉴权逻辑）
```

---

## 6. 迁移步骤与顺序

| 阶段 | 任务 | 验证方式 |
| --- | --- | --- |
| 1 | 后端初始化 + MySQL + 数据库层 | 能连接 MySQL，表创建成功 |
| 2 | 文档解析与入库（pdf/docx/txt/md） | 四种格式均可解析入库，Chunk 结构一致 |
| 3 | 知识库 CRUD + 种子数据 | 预置「法律领域」知识库并可新增知识库；检索日志落库 |
| 4 | 文档上传 + Celery 异步索引 | 上传四种格式文件，状态变为 completed |
| 5 | 检索服务适配多知识库 + 构建块开关 | 四种开关组合还原四策略，链路参数不变 |
| 6 | 对话服务 + 多轮 + 流式 | 前端能流式接收答案，引用正确溯源 |
| 7 | Vue3 前端基础框架 + 四页路由 | 问答/知识库/检索策略/日志四页可切换 |
| 8 | 知识库页 + 文档管理 | 新建知识库、上传文档、查看状态 |
| 9 | 问答页 + 引用 + 检索可视化 | 完整问答流程 |
| 10 | 检索策略页 + 日志页 | 切换开关生效，检索/系统日志可查 |
| 11 | 可观测 + 指标 | 查看 /metrics |
| 12 | Docker Compose 部署 | 一键启动所有服务 |

> **每阶段完成后自检**：运行 `git status`，结果只应出现 `product/` 下的**新增**文件；一旦发现 `demo/` 下有 modified / deleted，立即回退纠正 —— `demo/` 必须始终原样保留。

---

## 7. 注意事项与常见问题

1. MySQL 字符集必须 utf8mb4，否则中文和 emoji 会报错。
2. 异步 SQLAlchemy：使用 `async_sessionmaker` 和 `AsyncSession`，不要混用同步操作。
3. **阻塞操作与事件循环**：检索、精排、生成都是阻塞操作。在 async 应用里必须通过 `run_in_threadpool` 执行（或保持普通 `def` 端点让 FastAPI 自动进线程池），**不要写成 `async def` 再直接调同步代码**。
4. **模型预热**：Embedding 与 Rerank 在**后台线程**预热，不要阻塞 lifespan；否则启动期间服务完全不可用。
5. Celery 任务：任务函数参数用基本类型，不要传 ORM 对象。
6. 多知识库隔离：向量 collection 用 `kb_{kb_id}`、持久化目录 `indexes/chroma/kb_{kb_id}/`，BM25 索引用 `indexes/bm25/kb_{kb_id}.pkl`。
7. 流式输出：FastAPI 用 `StreamingResponse`，前端用 `EventSource`，注意 CORS 与代理配置；流式时先累积完整文本再做引用解析。
8. 文件存储：毕设可先用本地 `storage/` 目录，预留 MinIO 接口。
9. **多格式解析**：pdf/docx/txt/md 四种格式必须收敛到同一 Chunk 结构；新增格式时只在 `ingest/parsers/` 增加解析器并注册扩展名映射，不要改动切分与索引。上传白名单只放行这四种扩展名。
10. **检索策略构建块**：策略由 4 个互不重叠开关（vector / bm25 / fusion / rerank）组合而成，存 `knowledge_bases.enabled_strategies`；不要把策略名硬编码进链路，开关组合需能还原 demo 的四种策略。
11. **日志页数据来源**：检索日志来自 `retrieval_logs` 表；系统日志来自 loguru 落盘的 `logs/app.log`。
12. **种子数据**：预置「法律领域」知识库时只**只读引用**仓库根 `resource/法律领域/`，不复制、不改写。
13. **无登录**：新项目不做用户体系与鉴权，不建 users / kb_permissions 表，不留 JWT 配置与登录页。
14. 数据库迁移：每次改表结构都要生成 Alembic 迁移脚本。
15. **评估**：新项目**不做评估模块**，评估与结果全部留在 `demo/`（demo 中不使用 RAGAS，用自建 LLM-as-Judge）。不要在新项目引入评估相关的依赖、数据表或页面。
16. **入库一致性**：全部文件处理完后一次性写分块文件；入库结束校验"manifest 块数之和 == 索引条数"。
17. **依赖避坑**：不装 FlagEmbedding；不用 chromadb 1.x；pydantic 锁 2.9.x。
18. 测试：每个模块完成后写测试；测试若使用假 embedder，必须把索引路径重定向到临时目录，避免污染真实向量库。
19. **迁移边界（最高优先级）**：见文首「迁移边界与目录约定」——`demo/` 只读、产物写入 `product/`、路径以新项目根为基准、每阶段 `git status` 复核。
20. **新项目 `.gitignore`**：`product/` 下需自建 `.gitignore`，忽略 `data/`、`indexes/`、`storage/`、`logs/`、`.venv/`、`.env`；根 `.gitignore` 已忽略 `.venv/`、`__pycache__/`、`.hf-cache/` 等，对新目录同样生效。

---

