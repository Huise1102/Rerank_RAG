# 基于混合检索与 Rerank 的领域知识库系统

一个面向领域文档的问答系统：多路召回 + 融合 + 交叉编码器精排，为回答提供可溯源的引用。

---

## 功能特性

- 多知识库管理：新建 / 编辑（名称、简介、分块策略）/ 启用停用 / 删除，支持多个彼此隔离的知识库。
- 文档入库：支持 pdf / docx / txt / md 四种格式上传（含拖拽上传），解析、分块并建立向量与 BM25 双索引。
- 在线预览：pdf 使用浏览器内置阅读器，md 渲染为富文本，txt 等宽显示，docx 提示下载后用本地软件打开。
- 问答：三栏布局（左侧会话列表、中部对话区、右侧检索详情面板，两侧均可隐藏），支持流式（SSE）输出与答案引用溯源。
- 图像问答：基于 DeepSeek 原生视觉，图片以 base64 数据 URL 传入；仅传图未输入文字时跳过检索，直接走多模态模型。
- 检索策略可配置：向量召回 / BM25 召回 / RRF 融合 / 精排 Rerank 四个构建块可自由开关组合。
- 检索日志与系统日志：日志页分「检索日志」「系统日志」两个标签页。
- 全局深色模式：科技风配色，左下角切换，偏好保存于 localStorage。

---

## 技术栈

### 后端

- Web 框架：FastAPI + Uvicorn
- 数据访问：SQLAlchemy 2.0（async）+ aiomysql + Alembic，数据库 MySQL
- 数据模型：Pydantic v2 / pydantic-settings
- 异步任务：Celery（broker 与结果后端均为 Redis）
- 对象存储：local（默认，落本地 `storage/`）或 MinIO
- 可观测：loguru 日志 + prometheus-client 指标

### 前端

- 框架：Vue 3（`<script setup>`）+ Vite
- UI 组件：Element Plus + @element-plus/icons-vue
- 状态管理：Pinia；路由：vue-router；请求：axios；Markdown：marked

### 检索与模型

- 稠密召回：BAAI/bge-large-zh-v1.5（sentence-transformers，本地推理，维度 1024）
- 稀疏召回：BM25Okapi + jieba 中文分词
- 融合：RRF（默认）或加权融合
- 精排：BAAI/bge-reranker-v2-m3 交叉编码器（sentence-transformers CrossEncoder）
- 向量库：Chroma（LangChain Chroma 集成）
- 生成：DeepSeek（OpenAI 兼容接口）

---

## 目录结构

```
product/
├── backend/
│   ├── alembic/                数据库迁移脚本（versions/*）
│   ├── scripts/
│   │   ├── init_db.py          建库 + 建表（默认走 Alembic，可切 create_all）
│   │   └── seed_data.py        预置「法律领域」知识库并导入 resource 语料
│   ├── src/
│   │   ├── api/                main.py（应用入口）、deps.py、v1/（kb/document/retrieval/chat/log 路由）
│   │   ├── core/               config.py（全局配置）、logging.py
│   │   ├── db/                 引擎与会话（session.py/base.py）、models/（ORM 模型）
│   │   ├── ingest/             解析（parsers/）、分块（splitter.py）、索引写入（indexer.py）
│   │   ├── retrieval/          向量召回、BM25、RRF 融合、精排、链路组装（pipeline.py）
│   │   ├── generation/         Prompt 组装、DeepSeek 调用、引用解析
│   │   ├── services/           业务编排：kb / document / ingest / retrieval / chat
│   │   ├── schemas/            Pydantic 请求 / 响应模型
│   │   ├── tasks/              Celery 应用（celery_app.py）与文档解析任务
│   │   └── utils/              存储抽象（local/minio）、Redis、MinIO 客户端
│   ├── tests/                  pytest 冒烟测试
│   ├── requirements.txt
│   ├── .env.example            环境变量模板
│   ├── alembic.ini
│   ├── Dockerfile
│   └── docker-compose.yml      MySQL + Redis + MinIO + 后端（+ 可选 Celery worker）
└── frontend/
    ├── src/
    │   ├── api/                axios 封装与各模块接口（request / kb / document / chat / log）
    │   ├── components/         ChatMessage、RetrievalDetail、StrategyBuilder、CitationCard
    │   ├── layouts/            MainLayout.vue（左侧菜单 + 左下角深色模式开关）
    │   ├── router/             vue-router（四个业务页面）
    │   ├── stores/             Pinia：kb / chat
    │   ├── views/              ChatQuery / KnowledgeBase / RetrievalStrategy / Logs
    │   ├── assets/             theme.css（主题与深色变量）
    │   ├── App.vue
    │   └── main.js
    ├── index.html
    ├── package.json
    └── vite.config.js
```

---

## 快速开始

### 环境要求

- Python 3.11.9（Windows x64）
- Node.js 18+ 与 npm
- MySQL 8（必需）；Redis、MinIO 可选（Redis 缺失时问答不受影响，见「说明与限制」）
- Docker 可选（用于一键启动基础设施）

### 后端

在 `product\backend` 目录下执行（Windows PowerShell）：

```powershell
cd product\backend

# 1. 创建虚拟环境并安装依赖（推荐 uv）
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
# 也可以使用标准 venv：
#   python -m venv .venv
#   .\.venv\Scripts\Activate.ps1
#   pip install -r requirements.txt

# 2. 准备配置：复制模板后填写真实 DeepSeek 密钥
Copy-Item .env.example .env        # 编辑 .env，填写 RAG_LLM__API_KEY

# 3. 启动 MySQL（若本机未运行，可用 Docker 只拉起数据库与可选中间件）
docker compose up -d mysql redis minio

# 4. 激活环境
.\.venv\Scripts\Activate.ps1

# 5. 初始化数据库（建库 + 迁移建表）
python scripts\init_db.py

# 6. 灌入预置语料「法律领域」（只读引用仓库根 resource\法律领域\ 下的文件）
python scripts\seed_data.py

# 7. 启动后端
python -m src.api.main
```

后端默认监听 `http://127.0.0.1:8000`，交互式文档见 `http://127.0.0.1:8000/docs`。

### 前端

```powershell
cd product\frontend
npm install
npm run dev
```

### 访问地址

- 前端：http://localhost:5173
- 后端 API：http://localhost:8000（`/api/v1` 为业务前缀，`/health` 健康检查）
- 交互式文档：http://localhost:8000/docs

> 首次启动会在后台线程预热向量与精排模型（本地模型体积较大，首次加载需要一定时间）。`/health` 中的 `warmed_up` 反映预热进度。

---

## 配置说明

配置采用 `RAG_<分组>__<字段>` 的命名规则（双下划线表示嵌套层级），默认值写在 `src/core/config.py`，可通过 `.env` 或系统环境变量覆盖。`.env.example` 中涉及的关键字段如下（此处只列字段名，示例文件内为占位值）：

- 应用与语料
  - `RAG_RESOURCE_DIR`：预置语料目录（默认指向仓库根 `resource`）
- LLM（DeepSeek）
  - `RAG_LLM__API_KEY`：DeepSeek 密钥（形如 `sk-` 开头的字符串，**需自行填写**）
  - `RAG_LLM__MODEL`：对话模型（默认 `deepseek-chat`）
  - `RAG_LLM__BASE_URL`：接口地址（默认 `https://api.deepseek.com/v1`）
- 数据库与缓存
  - `RAG_DATABASE__URL`：SQLAlchemy 异步连接串（`mysql+aiomysql://<user>:<password>@127.0.0.1:3306/rag_system?charset=utf8mb4`）
  - `RAG_REDIS__URL`：Redis 连接串（Celery broker/backend）
- 对象存储
  - `RAG_STORAGE__TYPE`：`local` | `minio`（默认 `local`）
  - `RAG_MINIO__ENDPOINT` / `RAG_MINIO__ACCESS_KEY` / `RAG_MINIO__SECRET_KEY` / `RAG_MINIO__BUCKET`：MinIO 相关参数
- 本地模型
  - `RAG_EMBEDDING__OFFLINE`：是否只用本地缓存（默认 `true`）
  - `RAG_EMBEDDING__CACHE_DIR`：HuggingFace 模型缓存目录
- 检索链路参数（模板中默认注释掉，取代码默认值，一般无需修改）
  - `RAG_RETRIEVAL__DENSE_TOP_K`、`RAG_RETRIEVAL__SPARSE_TOP_K`、`RAG_RETRIEVAL__TOP_K`
  - `RAG_RETRIEVAL__FUSION`、`RAG_RETRIEVAL__RRF_K`
  - `RAG_RERANK__TOP_K`、`RAG_GENERATION__CONTEXT_MAX_CHARS`

安全提示：真实密钥与数据库口令请自行填写，不要提交到仓库。`product/.gitignore` 已忽略 `backend/.env`，请仅在 `.env` 中存放密钥。

---

## 检索链路说明

一次问答的检索默认走「双路召回 + 融合 + 精排」完整链路，核心参数如下：

1. 双路召回：向量（BGE 稠密）与 BM25（稀疏）各取 Top-50（`RAG_RETRIEVAL__DENSE_TOP_K` / `RAG_RETRIEVAL__SPARSE_TOP_K`）。
2. 融合：RRF（`score = Σ w_i / (k + rank_i)`，`k = 60`），融合后候选池保留 20 条（`RAG_RETRIEVAL__TOP_K`）。
3. 精排：交叉编码器 bge-reranker-v2-m3 对候选重排，取 Top-5（`RAG_RERANK__TOP_K`）。
4. 生成：Top-5 片段拼入上下文（预算 6000 字，`RAG_GENERATION__CONTEXT_MAX_CHARS`），交给 DeepSeek 生成带引用的答案。

链路采用「构建块可开关」模式：`vector`（向量召回）、`bm25`（BM25 召回）、`fusion`（融合）、`rerank`（精排）四个开关（存在知识库的 `enabled_strategies` 字段，默认全开）可自由组合。实际生效的策略名会在响应中以 `vector` | `bm25` | `hybrid` | `hybrid_rerank` 之一记录。若两路召回都开启但未开启融合，会按 RRF 融合执行并给出告警；若关闭了所有召回路径，则按双路混合执行。

---

## 主要 API 一览

业务接口统一挂载在 `/api/v1` 前缀下。

### 知识库

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/kb` | 知识库列表 |
| POST | `/kb` | 新建知识库 |
| GET | `/kb/{kb_id}` | 知识库详情 |
| PUT | `/kb/{kb_id}` | 更新知识库（名称/简介/分块策略等） |
| PATCH | `/kb/{kb_id}/strategies` | 更新检索构建块开关 |
| DELETE | `/kb/{kb_id}` | 删除知识库（含索引与文档） |

### 文档

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/kb/{kb_id}/documents` | 上传文档（pdf/docx/txt/md） |
| GET | `/kb/{kb_id}/documents` | 知识库文档列表 |
| GET | `/documents/{doc_id}` | 文档详情 |
| GET | `/documents/{doc_id}/file` | 查看/下载原始文档（`download=true` 下载，否则内联预览） |
| DELETE | `/documents/{doc_id}` | 删除文档 |

### 检索

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/kb/{kb_id}/retrieve` | 检索调试（只检索不生成，返回结果与耗时） |

### 对话

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/chat` | 问答（非流式） |
| POST | `/chat/stream` | 问答（流式 SSE） |
| GET | `/conversations` | 会话列表（可按 `kb_id` 过滤） |
| GET | `/conversations/{conversation_id}/messages` | 会话消息 |
| DELETE | `/conversations/{conversation_id}` | 删除会话 |

### 日志与服务

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/logs/retrieval` | 检索日志（分页，可按 `kb_id` 过滤） |
| GET | `/logs/system` | 系统日志（`logs/app.log` 尾部，可按级别/关键字过滤） |
| GET | `/health` | 健康检查（含 `warmed_up`、Redis 状态），另有等价路径 `/api/v1/health` |
| POST | `/warmup` | 手动触发热身（幂等） |
| GET | `/config` | 当前生效配置（密钥脱敏） |
| GET | `/metrics` | Prometheus 指标 |

---

## 说明与限制

- 单用户、无认证：系统没有登录页、用户体系与鉴权中间件，所有接口可直接访问。
- 不含评估模块，也不含 Agent 检索决策模块，请勿据此预期相关能力。
- Redis 可选：Redis 未运行时不影响问答（检索与生成均在线程池内执行）。仅上传文档后的异步解析入库链路受影响——Celery broker 不可用时，系统会降级为在线程池内同步入库，只是不再异步。
- 图片不持久化：问答中上传的图片不会落库，重新打开历史会话时仅显示占位。图片以 base64 数据 URL 传递，仅在当次请求中随上下文进入多模态模型。
- 检索与 LLM 调用为阻塞操作，均在线程池中执行，避免阻塞 FastAPI 事件循环。
- 预置知识库「法律领域」的语料为只读引用（仓库根 `resource\法律领域\` 下的文件），入库时不复制、不改写原文件。
- 首次运行需联网或已具备本地模型缓存（bge-large-zh-v1.5 与 bge-reranker-v2-m3）；`RAG_EMBEDDING__OFFLINE=true` 时仅使用本地缓存，不访问 HuggingFace。
- `.env` 不入库（已被 `product/.gitignore` 忽略），请在本地填写真实密钥。
