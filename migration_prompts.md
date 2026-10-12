# RAG 领域问答系统迁移提示词全集

本文档用于将当前基于 **Streamlit + FastAPI** 的 RAG 问答系统，迁移为基于 **FastAPI + Vue3 + MySQL + Redis + MinIO + Celery** 的产品级领域知识库系统。

---

## 修订说明（相对原文档的改动）

本文档在原稿基础上，按**当前项目的真实实现**做了校正，重点如下：

1. **纠正失实描述**：当前后端并非"只有 `/query` 和 `/health`"；评估模块**不使用 RAGAS**（项目明确弃用）；依赖清单中的 **`FlagEmbedding` 必须删除**（项目刻意不装）。
2. **补充必须原样保留的链路参数**：双路召回 Top-50 → RRF(k=60) → 候选 20 → 精排 Top-5 → 上下文预算 6000 字；分块 `fixed(500/50/max800)` 与 `recursive(500/50/separators/max1200)`。
3. **补充必须原样保留的工程约束**：重排模型**后台线程预热**（不得阻塞启动）；检索/生成是**阻塞**操作，迁移到 async 应用后**必须走线程池**，不得用 `async def` 直接包同步代码。
4. **补充必须原样保留的核心资产**：`citation.py`（宽松解析 + 严格映射 + 越界编号幻觉信号）、BGE 查询指令前缀、确定性 `chunk_id`、`ingest_manifest.json` 增量清单。
5. **版本对齐**：Python 3.11.9、LangChain 0.2.17、Chroma 0.5.20、pydantic 锁 2.9.x，与现行 `requirements.txt` 一致。
6. **评估口径对齐**：检索评估零 token 可复现；分块策略消融**必须用页级判定**；Top-k 选档依据是"召回饱和点"而非"指标最高档"；生成评估采用**分层抽样**。

---

## 0. 项目背景与迁移目标

### 0.1 当前项目状态（已按实际代码校正）

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

构建一个多用户、多知识库、多轮对话、可观测、可评估的领域知识库系统，具备：

- 用户注册/登录，JWT 鉴权，RBAC 权限
- 知识库 CRUD，文档上传、解析、索引、删除
- 多知识库隔离（每个知识库独立的向量索引和 BM25 索引）
- 检索 pipeline 可配置（纯向量 / 纯 BM25 / 混合 / 混合+Rerank）
- 多轮对话，会话管理，流式输出，引用溯源
- 离线评估与在线 A/B 对比，可视化指标
- 结构化日志、指标、链路追踪
- Docker Compose 一键部署

### 0.3 目标技术栈

| 层级 | 技术选型 |
| --- | --- |
| 前端 | Vue3 + Vite + Element Plus + Pinia + Vue Router + Axios + ECharts |
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
| 评估 | **自建 LLM-as-Judge**（不引入 RAGAS） |
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
│  前端 Vue3 + Element Plus                                │
│  ├─ 登录/注册                                            │
│  ├─ 知识库管理（列表/创建/上传/预览）                     │
│  ├─ 对话界面（多轮/引用/检索可视化）                      │
│  ├─ 评估面板（策略对比/指标图表）                         │
│  └─ 系统设置（用户/角色/模型配置）                        │
└─────────────────────────────────────────────────────────┘
                          ↓ RESTful API
┌─────────────────────────────────────────────────────────┐
│  后端 FastAPI                                            │
│  ├─ 认证鉴权（JWT + RBAC）                               │
│  ├─ 知识库服务（CRUD + 文档管理）                         │
│  ├─ 检索服务（可配置 pipeline + 插件）                    │
│  ├─ 对话服务（多轮 + 会话管理 + 流式）                    │
│  ├─ 评估服务（离线评估 + 在线 A/B）                       │
│  └─ 可观测（日志/指标/追踪）                              │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│  数据层                                                  │
│  ├─ MySQL：用户/知识库/文档/会话/日志/评估元数据           │
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
│  ├─ LLM：DeepSeek API                                    │
│  └─ 评估：自建 LLM-as-Judge                               │
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
│   │   │   ├── auth.py          # 登录/注册
│   │   │   ├── kb.py            # 知识库管理
│   │   │   ├── document.py      # 文档管理
│   │   │   ├── chat.py          # 对话（含 /chat/stream）
│   │   │   ├── retrieval.py     # 检索调试
│   │   │   └── evaluation.py    # 评估
│   │   ├── deps.py              # 依赖注入
│   │   └── main.py              # FastAPI 入口
│   ├── core/
│   │   ├── config.py            # 配置（沿用 11 分组 + RAG_<分组>__<字段> 覆盖）
│   │   ├── security.py          # JWT/密码哈希
│   │   └── logging.py           # 日志
│   ├── db/
│   │   ├── base.py              # Base 和 engine
│   │   ├── session.py           # session 依赖
│   │   └── models/              # ORM 模型
│   │       ├── user.py
│   │       ├── knowledge_base.py
│   │       ├── document.py
│   │       ├── conversation.py
│   │       ├── message.py
│   │       ├── retrieval_log.py
│   │       └── evaluation.py
│   ├── schemas/                 # Pydantic 模型
│   │   ├── user.py
│   │   ├── kb.py
│   │   ├── document.py
│   │   ├── chat.py
│   │   └── evaluation.py
│   ├── services/                # 业务逻辑
│   │   ├── auth_service.py
│   │   ├── kb_service.py
│   │   ├── document_service.py
│   │   ├── retrieval_service.py
│   │   ├── chat_service.py
│   │   └── evaluation_service.py
│   ├── retrieval/               # 复用现有检索模块（只加 kb_id 适配）
│   │   ├── vector_retriever.py
│   │   ├── bm25_retriever.py
│   │   ├── hybrid_retriever.py
│   │   ├── reranker.py
│   │   └── pipeline.py
│   ├── generation/              # 复用现有生成模块
│   │   ├── prompt.py
│   │   ├── llm.py
│   │   └── citation.py
│   ├── ingest/                  # 文档解析与索引
│   │   ├── loader.py
│   │   ├── splitter.py
│   │   └── indexer.py
│   ├── tasks/                   # Celery 任务
│   │   ├── celery_app.py
│   │   ├── document_tasks.py
│   │   └── evaluation_tasks.py
│   └── utils/
│       ├── minio_client.py
│       └── redis_client.py
├── tests/
├── scripts/
│   ├── init_db.py
│   └── create_admin.py
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

### 2.2 前端目录结构（目标）

```
frontend/
├── public/
├── src/
│   ├── api/                     # Axios 封装
│   │   ├── request.js
│   │   ├── auth.js
│   │   ├── kb.js
│   │   ├── document.js
│   │   ├── chat.js
│   │   └── evaluation.js
│   ├── assets/
│   ├── components/
│   │   ├── ChatMessage.vue
│   │   ├── CitationCard.vue
│   │   ├── RetrievalDetail.vue
│   │   └── StrategySelect.vue
│   ├── layouts/
│   │   ├── MainLayout.vue
│   │   └── AuthLayout.vue
│   ├── router/
│   │   └── index.js
│   ├── stores/
│   │   ├── user.js
│   │   ├── kb.js
│   │   └── chat.js
│   ├── views/
│   │   ├── Login.vue
│   │   ├── Register.vue
│   │   ├── KnowledgeBaseList.vue
│   │   ├── KnowledgeBaseDetail.vue
│   │   ├── DocumentList.vue
│   │   ├── Chat.vue
│   │   ├── Evaluation.vue
│   │   └── Settings.vue
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

-- 用户表
CREATE TABLE users (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(100) UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role ENUM('admin', 'user') DEFAULT 'user',
    is_active BOOLEAN DEFAULT TRUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 知识库表
CREATE TABLE knowledge_bases (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    owner_id BIGINT NOT NULL,
    visibility ENUM('public', 'private') DEFAULT 'private',
    chunk_strategy VARCHAR(50) DEFAULT 'recursive',
    retrieval_strategy VARCHAR(50) DEFAULT 'hybrid_rerank',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 知识库权限表（可选，用于共享）
CREATE TABLE kb_permissions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    kb_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    permission ENUM('read', 'write', 'admin') DEFAULT 'read',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (kb_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE KEY uk_kb_user (kb_id, user_id)
);

-- 文档表
CREATE TABLE documents (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    kb_id BIGINT NOT NULL,
    filename VARCHAR(255) NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    file_size BIGINT,
    file_type VARCHAR(50),
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
    user_id BIGINT NOT NULL,
    kb_id BIGINT NOT NULL,
    title VARCHAR(200),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (kb_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    INDEX idx_user_kb (user_id, kb_id)
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
    user_id BIGINT,
    kb_id BIGINT,
    query TEXT NOT NULL,
    strategy VARCHAR(50),
    top_k INT,
    latency FLOAT,
    result_count INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_kb_created (kb_id, created_at),
    INDEX idx_user_created (user_id, created_at)
);

-- 评估记录表
CREATE TABLE evaluations (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    kb_id BIGINT NOT NULL,
    testset_name VARCHAR(100),
    strategy VARCHAR(50),
    metrics JSON,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (kb_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE
);
```

---

## 4. 后端模块设计与提示词

### 4.1 项目初始化与依赖

给 AI 的提示词：

```
请帮我初始化一个新的 FastAPI 后端项目，目录结构见附件。技术栈与版本必须与现有项目对齐：
- Python 3.11.9
- FastAPI + Uvicorn
- SQLAlchemy 2.0 (async) + aiomysql
- Alembic
- pydantic >=2.9,<2.10（不要用 2.10+，会与 langchain-core 0.2.x 冲突）
- python-jose[cryptography] + passlib[bcrypt]
- celery + redis
- minio
- langchain 0.2.17（只用于文档加载/切分）
- chromadb >=0.5.5,<0.6.0
- rank_bm25 + jieba
- sentence-transformers
- pdfplumber
- loguru

注意：
1. 不要安装 FlagEmbedding（reranker 用 sentence-transformers 的 CrossEncoder 加载即可）。
2. 不要引入 RAGAS / datasets（评估用自建 LLM-as-Judge）。
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
   - user.py, knowledge_base.py, document.py, conversation.py, message.py, retrieval_log.py, evaluation.py
   - 使用 SQLAlchemy 2.0 风格（Mapped, mapped_column）
   - JSON 字段用 JSON 类型
   - 时间字段用 server_default=func.now()
4. alembic 配置：
   - alembic init alembic
   - 修改 env.py 支持 async 和 Base.metadata
   - 生成初始迁移脚本
请生成完整代码。
```

### 4.3 认证与用户模块

给 AI 的提示词：

```
请实现认证与用户模块：
1. src/core/security.py：
   - 密码哈希：passlib bcrypt
   - JWT 生成与验证：python-jose，HS256，过期时间 7 天
   - 从环境变量读取 SECRET_KEY（禁止硬编码）
2. src/schemas/user.py：
   - UserCreate: username, email, password
   - UserLogin: username, password
   - UserOut: id, username, email, role, created_at
   - Token: access_token, token_type
3. src/services/auth_service.py：
   - register_user: 检查用户名/邮箱唯一，哈希密码，入库
   - authenticate_user: 验证用户名密码
   - create_access_token
4. src/api/v1/auth.py：
   - POST /auth/register
   - POST /auth/login (OAuth2PasswordRequestForm)
   - GET /auth/me (需要 JWT)
5. src/api/deps.py：
   - get_current_user: 从 JWT 解析用户
   - get_current_admin: 要求 admin 角色
请生成完整代码。
```

### 4.4 知识库管理模块

给 AI 的提示词：

```
请实现知识库管理模块：
1. src/schemas/kb.py：
   - KBCreate: name, description, visibility, chunk_strategy, retrieval_strategy
   - KBUpdate: 可选字段
   - KBOut: id, name, description, owner_id, visibility, chunk_strategy, retrieval_strategy, created_at
2. src/services/kb_service.py：
   - create_kb: 当前用户为 owner
   - list_kbs: 返回当前用户可见的知识库（自己创建的 + public + 有权限的）
   - get_kb: 检查权限
   - update_kb: 只有 owner 或 admin 可改
   - delete_kb: 只有 owner 或 admin 可删，同时删除关联的向量索引、BM25 索引、文档
3. src/api/v1/kb.py：
   - GET /kb
   - POST /kb
   - GET /kb/{kb_id}
   - PUT /kb/{kb_id}
   - DELETE /kb/{kb_id}
请生成完整代码。

多知识库隔离要求：
- Chroma collection_name = f"kb_{kb_id}"，持久化目录 indexes/chroma/kb_{kb_id}/
- BM25 索引文件 indexes/bm25/kb_{kb_id}.pkl
- 保留现有 chunk_id 生成规则（文件名 + 页码 + 块序号 + 正文哈希），确保同一 kb 内同样输入必然得到同样 id
- 每个 kb 维护独立的 ingest_manifest.json（sha256 + 块数），用于增量入库
```

### 4.5 文档管理模块

给 AI 的提示词：

```
请实现文档管理模块：
1. src/schemas/document.py：
   - DocumentOut: id, kb_id, filename, file_size, status, chunk_count, error_msg, created_at
2. src/services/document_service.py：
   - upload_document: 保存文件到 MinIO 或本地 storage/{kb_id}/，创建文档记录，状态 pending
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

入库必须保留现有两道防线：
1. 全部文件处理完后一次性写入分块文件（不要在逐文件循环里写，否则后一个文件会覆盖前一个）。
2. 入库结束做一致性校验：manifest 记录的块数之和 == 索引实际条数，不一致则报错中止。
```

### 4.6 检索服务模块（复用与适配）

给 AI 的提示词：

```
请将现有的 src/retrieval/ 模块迁移到新项目，并适配多知识库。只做"加 kb_id 参数"的适配，不要改动检索算法本身：

1. 保留原有检索器：vector_retriever.py, bm25_retriever.py, hybrid_retriever.py, reranker.py, pipeline.py
   - 修改为接受 kb_id，从对应 Chroma collection 和 BM25 索引文件加载

2. 索引存储路径：
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
   - retrieve(kb_id, query, strategy, top_k) -> List[Document]
   - 根据 kb 配置的 retrieval_strategy 选择 pipeline
   - 记录检索日志到 retrieval_logs 表

5. src/api/v1/retrieval.py：
   - POST /kb/{kb_id}/retrieve：调试用，返回检索结果和耗时（对应现有 /retrieve 与 /strategies）

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
   - create_conversation / get_conversation（检查权限）/ list_conversations / delete_conversation
   - chat: 核心逻辑
     * 如果无 conversation_id，创建新会话
     * 保存用户消息
     * 调用 retrieval_service.retrieve
     * 调用 generation.llm.generate
     * 保存助手消息（含 citations, retrieved_docs, latency）
     * 返回 ChatResponse
3. 生成侧必须复用现有 generation 模块，不得重写：
   - prompt.py：上下文预算裁剪（按相关性降序累加至超预算即停；单条超预算一半时截断而非丢弃）
   - llm.py：两层重试（SDK 负责连接错误/429/5xx，应用层只重试超时并检查剩余预算）；
            调用方显式指定 model 时不降级
   - citation.py：宽松解析（兼容 [1][2]、[1,2]、[1、2]、[1-3]、【1】、（1））
                + 严格映射回上下文编号
                + 区分 unused_contexts（送入未引用）与 invalid_indices（引用不存在的编号=幻觉信号）
   - 生成参数 temperature=0.3；引用格式要求作为独立指令片段动态追加，不要在系统提示词里重复
4. 流式输出：
   - 实现 async generator，逐 token yield
   - 使用 FastAPI StreamingResponse，media_type="text/event-stream"（对应现有 /query/stream）
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

### 4.8 评估服务模块

给 AI 的提示词：

```
请实现评估服务模块。注意：评估层不参与线上链路，且不使用 RAGAS：
1. src/schemas/evaluation.py：
   - EvaluationRequest: kb_id, testset_name, strategies (列表)
   - EvaluationOut: id, kb_id, testset_name, strategy, metrics, created_at
2. src/services/evaluation_service.py：
   - run_evaluation: 对指定 kb 和策略列表跑测试集，计算 Recall@1/3/5/10/20、MRR、nDCG、Precision@k、平均首命中名次
   - 测试集从 data/testsets/{testset_name}.json 加载；先做 chunk_id 存在性校验
   - 检索评估零 token 可复现；结果存入 evaluations 表
3. 生成质量评估（自建 LLM-as-Judge）：
   - 裁判 temperature=0、强制 JSON 输出、解析失败剔除而非计 0 分
   - 评分维度：faithfulness、answer_relevancy（1-5 分）
   - 保留裁判原文与打分理由，便于事后解释
   - 样本按类别分层抽样（语义泛化 / 术语精确 / 多跳推理轮流取），避免小样本只覆盖一类
4. 消融实验口径：
   - 分块策略消融必须用"页级判定"（文件名 + 页码），不能用 chunk_id；
     换分块策略后页码相同但 chunk_id 完全不同，按 id 比对会让 fixed 档全为 0
   - Rerank Top-k 消融：选档依据是"召回饱和点"而非"指标最高档"
5. src/tasks/evaluation_tasks.py：Celery 任务 run_evaluation_task
6. src/api/v1/evaluation.py：
   - POST /evaluation/run（触发异步任务）
   - GET /evaluation/results?kb_id=...
   - GET /evaluation/compare?kb_id=...&strategies=...
请生成完整代码。
```

### 4.9 可观测模块

给 AI 的提示词：

```
请实现可观测模块：
1. src/core/logging.py：
   - 用 loguru 配置结构化日志
   - 输出到控制台和文件 logs/app.log
   - 记录请求 ID、用户 ID、耗时
2. 中间件：
   - 在 FastAPI 中加中间件，记录每个请求的方法、路径、状态码、耗时
   - 注入 request_id
3. 指标（可选）：
   - 用 prometheus-client 暴露 /metrics
   - 记录 QPS、延迟直方图、错误计数
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
1. 创建 FastAPI 实例，title="基于混合检索与Rerank的RAG领域问答系统"
2. 加 CORS 中间件
3. 加请求日志中间件
4. 注册路由：
   - /api/v1/auth
   - /api/v1/kb
   - /api/v1/documents
   - /api/v1/retrieval
   - /api/v1/chat
   - /api/v1/evaluation
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
请实现 src/core/config.py，用 pydantic-settings，并沿用现有配置结构：
- 按功能分为 11 个分组：paths, chunking, embedding, retrieval, rerank, llm, generation, api, frontend, evaluation, logging
- 支持环境变量覆盖，命名规则 RAG_<分组>__<字段>（如 RAG_LLM__API_KEY、RAG_EMBEDDING__OFFLINE）
- 提供打印全部生效配置的能力（密钥脱敏，对应现有 /config 接口）

字段至少包括：
- DATABASE_URL（MySQL 连接串）
- REDIS_URL（Redis 连接串）
- SECRET_KEY（JWT 密钥）
- ACCESS_TOKEN_EXPIRE_MINUTES: 默认 10080（7 天）
- DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL（https://api.deepseek.com）
- EMBEDDING_MODEL_PATH: BAAI/bge-large-zh-v1.5（含查询指令前缀配置）
- RERANKER_MODEL_PATH: BAAI/bge-reranker-v2-m3
- MINIO_ENDPOINT / MINIO_ACCESS_KEY / MINIO_SECRET_KEY / MINIO_BUCKET
- STORAGE_TYPE: local / minio
- CHROMA_PERSIST_DIR: indexes/chroma
- BM25_INDEX_DIR: indexes/bm25
- 检索链路参数：recall_top_k=50, fusion_top_k=20, rerank_top_k=5, rrf_k=60, context_max_chars=6000
所有配置从 .env 读取，提供 .env.example。
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
- ECharts
目录结构见附件。配置代理：/api -> http://localhost:8000
```

### 5.2 登录/注册页

给 AI 的提示词：

```
请实现登录和注册页面：
1. Login.vue：用户名/密码表单，调用 POST /api/v1/auth/login，保存 token 到 Pinia 和 localStorage
2. Register.vue：用户名/邮箱/密码，调用 POST /api/v1/auth/register
3. 路由守卫：未登录跳转 /login
4. Axios 拦截器：请求带 Authorization: Bearer {token}，401 跳转登录
```

### 5.3 知识库管理页

给 AI 的提示词：

```
请实现知识库管理页面：
1. KnowledgeBaseList.vue：
   - 表格展示知识库：名称、描述、可见性、创建时间
   - 按钮：创建知识库、进入详情、删除
   - 创建对话框：名称、描述、可见性、分块策略、检索策略
2. KnowledgeBaseDetail.vue：
   - 标签页：文档管理、对话、评估
   - 文档管理：上传、列表、状态、删除
   - 对话：跳转到 Chat 页并带上 kb_id
```

### 5.4 文档管理页

给 AI 的提示词：

```
请实现文档管理组件：
1. 上传：el-upload，调用 POST /api/v1/kb/{kb_id}/documents
2. 列表：表格展示 filename, size, status, chunk_count, created_at
3. 状态轮询：上传后每 3 秒刷新列表，直到状态为 completed 或 failed
4. 删除：确认后调用 DELETE /api/v1/documents/{doc_id}
```

### 5.5 对话页

给 AI 的提示词：

```
请实现对话页面 Chat.vue：
1. 左侧：会话列表，可新建、切换、删除
2. 右侧：对话区域
   - 消息气泡：用户右对齐，AI 左对齐
   - AI 回答支持 Markdown 渲染
   - 引用编号 [1][2] 可点击，滚动到引用卡片
   - 引用卡片：展示来源文件、页码、原文
   - 检索详情：可折叠，展示 Top-5 文档块与两路召回得分
3. 底部：输入框，策略选择下拉框（纯向量 / 纯 BM25 / 混合 / 混合+Rerank）
4. 流式输出：用 EventSource 接收 POST /api/v1/chat/stream
5. 调用接口：
   - POST /api/v1/chat/stream
   - GET /api/v1/conversations
   - GET /api/v1/conversations/{id}/messages
```

### 5.6 评估面板

给 AI 的提示词：

```
请实现评估页面 Evaluation.vue：
1. 选择知识库、测试集、策略（多选）
2. 点击"运行评估"，调用 POST /api/v1/evaluation/run
3. 轮询任务状态，完成后展示结果
4. 结果展示：
   - 表格：策略 vs Recall@1/3/5、MRR、nDCG、平均首命中名次
   - 柱状图：ECharts 对比不同策略
   - 消融实验：分块策略、Rerank Top-k
```

### 5.7 系统设置

给 AI 的提示词：

```
请实现系统设置页面 Settings.vue：
1. 个人信息：修改密码
2. 用户管理（仅 admin）：用户列表、角色修改、禁用
3. 模型配置：LLM/Embedding/Rerank 选择（预留）
```

### 5.8 API 封装与状态管理

给 AI 的提示词：

```
请实现前端 API 封装和 Pinia store：
1. src/api/request.js：Axios 实例，baseURL='/api/v1'，拦截器
2. src/api/auth.js, kb.js, document.js, chat.js, evaluation.js
3. src/stores/user.js：token, userInfo, login, logout
4. src/stores/kb.js：知识库列表、当前知识库
5. src/stores/chat.js：会话列表、当前会话、消息列表
```

---

## 6. 迁移步骤与顺序

| 阶段 | 任务 | 验证方式 |
| --- | --- | --- |
| 1 | 后端初始化 + MySQL + 数据库层 | 能连接 MySQL，表创建成功 |
| 2 | 认证模块 | 注册/登录/获取当前用户 |
| 3 | 知识库 CRUD | 创建/列表/删除知识库 |
| 4 | 文档上传 + Celery 异步索引 | 上传 PDF，状态变为 completed |
| 5 | 检索服务适配多知识库 | 指定 kb_id 检索返回结果，链路参数不变 |
| 6 | 对话服务 + 多轮 + 流式 | 前端能流式接收答案，引用正确溯源 |
| 7 | Vue3 前端基础框架 + 登录 | 登录后进入主页 |
| 8 | 知识库管理页 + 文档管理页 | 上传文档、查看状态 |
| 9 | 对话页 + 引用 + 检索可视化 | 完整问答流程 |
| 10 | 评估模块 + 前端评估面板 | 跑评估并出图表 |
| 11 | 可观测 + 日志 + 指标 | 查看日志和 /metrics |
| 12 | Docker Compose 部署 | 一键启动所有服务 |

---

## 7. 给 AI 的通用提示词模板

当你需要 AI 帮你迁移某个模块时，使用以下模板：

```
【上下文】
我正在将一个 RAG 问答系统迁移为 FastAPI + Vue3 + MySQL 的产品级系统。
当前项目结构：[粘贴相关目录]
现有代码：[粘贴需要复用的代码]
目标架构：[粘贴目标目录结构]

【任务】
请帮我实现 [模块名称]，要求：
1. [具体要求1]
2. [具体要求2]
3. [具体要求3]

【约束】
- 使用 SQLAlchemy 2.0 async；但检索/生成等阻塞操作必须走线程池，禁止用 async def 直接包同步代码
- 使用 Pydantic v2（锁 2.9.x）
- 多知识库隔离：每个 kb 有独立的向量 collection 与 BM25 索引文件
- 保留现有检索链路参数（50/20/5、RRF k=60、上下文预算 6000 字）与 BGE 查询指令前缀
- 保留现有生成逻辑（prompt 预算裁剪、两层重试、citation 宽松解析+严格映射），只做适配
- 重排模型后台线程预热，禁止阻塞启动
- 代码注释清晰，符合 PEP8

【输出】
请生成完整代码文件，并说明如何集成到现有项目。
```

---

## 8. 注意事项与常见问题

1. MySQL 字符集必须 utf8mb4，否则中文和 emoji 会报错。
2. 异步 SQLAlchemy：使用 `async_sessionmaker` 和 `AsyncSession`，不要混用同步操作。
3. **阻塞操作与事件循环**：检索、精排、生成都是阻塞操作。在 async 应用里必须通过 `run_in_threadpool` 执行（或保持普通 `def` 端点让 FastAPI 自动进线程池），**不要写成 `async def` 再直接调同步代码**。
4. **模型预热**：Embedding 与 Rerank 在**后台线程**预热，不要阻塞 lifespan；否则启动期间服务完全不可用。
5. Celery 任务：任务函数参数用基本类型，不要传 ORM 对象。
6. 多知识库隔离：向量 collection 用 `kb_{kb_id}`、持久化目录 `indexes/chroma/kb_{kb_id}/`，BM25 索引用 `indexes/bm25/kb_{kb_id}.pkl`。
7. 流式输出：FastAPI 用 `StreamingResponse`，前端用 `EventSource`，注意 CORS 与代理配置；流式时先累积完整文本再做引用解析。
8. 文件存储：毕设可先用本地 `storage/` 目录，预留 MinIO 接口。
9. JWT 密钥：从环境变量读取，不要硬编码。
10. 数据库迁移：每次改表结构都要生成 Alembic 迁移脚本。
11. **评估框架**：不使用 RAGAS（依赖重且与 pydantic 2.9 有兼容风险）；用自建 LLM-as-Judge，裁判原文与打分理由一并存档以便解释。
12. **入库一致性**：全部文件处理完后一次性写分块文件；入库结束校验"manifest 块数之和 == 索引条数"。
13. **消融口径**：分块策略消融用页级判定（文件名+页码），不能用 chunk_id。
14. **依赖避坑**：不装 FlagEmbedding；不用 chromadb 1.x；pydantic 锁 2.9.x。
15. 测试：每个模块完成后写测试；测试若使用假 embedder，必须把索引路径重定向到临时目录，避免污染真实向量库。

---

文档结束。

将本文档保存为 `migration_prompts.md`，按章节复制提示词给 AI，即可逐步完成迁移。