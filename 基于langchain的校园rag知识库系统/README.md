# 基于 LangChain 的校园 RAG 知识库系统

面向校园制度问答的独立系统。检索与生成链路基于 LangChain 构建，包含管理端与用户端两个前端入口、统一身份认证、角色权限、知识库管理、混合检索问答、引用溯源与反馈闭环。

示例语料位于 `resource/示例/`，内容均为示例文档，不是学校正式制度。

## 一、技术栈

| 层次 | 组件 |
| --- | --- |
| 后端 | FastAPI、Uvicorn、SQLAlchemy 2 异步 ORM、Alembic |
| 数据库 | MySQL 8 |
| 认证 | bcrypt、JWT、服务端可撤销会话表 |
| 检索 | LangChain、Chroma、BM25Retriever、EnsembleRetriever、CrossEncoderReranker |
| 生成 | LCEL 链、ChatOpenAI（兼容 DeepSeek 等 OpenAI 协议服务） |
| 限流 | Redis，不可用时退化为进程内计数 |
| 前端 | Vue 3、Vite、Element Plus、Pinia、Vue Router |

## 二、目录结构

```text
.
├── backend/                 后端服务、迁移、脚本、测试
│   ├── alembic/             数据库迁移
│   ├── scripts/             初始化、健康检查、冒烟测试
│   ├── src/
│   │   ├── api/             路由与鉴权依赖
│   │   ├── core/            配置、安全、日志
│   │   ├── db/              ORM 模型与会话
│   │   ├── ingest/          LangChain 加载、切分、索引流水线
│   │   ├── retrieval/       Chroma、BM25、融合检索、精排
│   │   ├── generation/      LCEL 链、提示模板、模型工厂
│   │   ├── services/        认证、问答、反馈、设置编排
│   │   ├── schemas/         请求与响应模型
│   │   └── utils/           限流、脱敏
│   └── tests/
├── frontend/                双模式前端
├── resource/示例/           示例语料
├── docs/                    需求与阶段文档
├── scripts/dev.ps1          本地启动脚本
├── .env.example             配置模板
└── README.md
```

## 三、环境要求

- Windows 10 及以上，PowerShell 5.1 及以上
- Python 3.11
- Node.js 18 及以上
- MySQL 8.0
- Redis 可选，未启动时限流使用进程内计数
- 首次索引会下载嵌入模型与精排模型，默认通过 `HF_ENDPOINT=https://hf-mirror.com` 镜像下载，模型缓存放在项目内 `.hf-cache/`

端口与库名默认值：

| 项目 | 默认值 |
| --- | --- |
| 后端 | 8004 |
| 管理端 | 5174 |
| 用户端 | 5175 |
| 数据库 | rag_campus |
| Redis 库号 | 4 |

## 四、从零启动

### 1. 准备数据库

使用本机 MySQL 8，或使用项目内 `docker-compose.yml`。

使用 compose 时端口为 3308 与 6380，需要相应修改 `.env` 中的 `MYSQL_PORT` 与 `REDIS_URL`。

数据库连接信息以 `.env` 为准，初始化脚本会自动创建 `rag_campus` 库，不会删除已有数据库。

> 注意：MySQL 在 Windows 上无法使用包含中文的数据目录。若把 MySQL 数据目录放在本项目路径下，需改用纯英文路径或使用默认安装目录。

### 2. 配置环境变量

```powershell
Copy-Item .env.example .env
```

按实际情况修改：

- `SECRET_KEY`：随机长字符串
- `MYSQL_PASSWORD`：数据库口令
- `LLM_API_KEY`：大模型密钥，未配置时问答接口返回 503
- `ADMIN_INITIAL_PASSWORD`：可留空，留空时由初始化脚本随机生成并只显示一次
- `HF_ENDPOINT`：模型下载地址，默认使用 `https://hf-mirror.com`，能直连模型仓库时可改为 `https://huggingface.co`
- `EMBEDDING_MODEL`、`RERANKER_MODEL`：嵌入与精排模型，默认 `BAAI/bge-small-zh-v1.5` 与 `BAAI/bge-reranker-base`

`.env` 已被 `.gitignore` 忽略，不要提交真实密钥。

模型缓存说明：后端启动时把 `HF_HOME` 与 `SENTENCE_TRANSFORMERS_HOME` 指向项目内 `.hf-cache/`。已下载过模型时，把对应模型目录复制到 `.hf-cache/hub/` 下即可离线复用，不必重复下载。例如复用本机已有的 `BAAI/bge-reranker-v2-m3` 时，把 `.env` 中的 `RERANKER_MODEL` 改为该模型名并保证缓存目录存在。

本地大模型测试：系统走 OpenAI 兼容协议，可直接接 Ollama。先执行 `ollama serve`，在管理端「模型配置」页切到「本地模型」，填写服务地址并把模型改为本机已安装的模型名，保存后下一次提问立即生效，无需重启后端。`.env` 中的 `LLM_PROVIDER`、`OLLAMA_BASE_URL`、`OLLAMA_MODEL` 只作为默认值，页面保存后以数据库配置为准。CPU 环境下 8B 模型首个增量约需三十秒，属正常现象。

### 3. 初始化后端

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\init_db.py
```

初始化脚本会完成：

1. 创建数据库（如不存在）
2. 执行 Alembic 迁移
3. 写入三条默认检索策略
4. 创建一个空的示例知识库
5. 创建初始管理员，并打印初始密码或提示密码来自环境变量

初始管理员首次登录必须修改密码。

### 4. 启动后端

```powershell
.\.venv\Scripts\python.exe -m uvicorn src.main:app --app-dir backend --host 127.0.0.1 --port 8004
```

健康检查：

```powershell
.\.venv\Scripts\python.exe backend\scripts\health_check.py
```

### 5. 安装并启动前端

```powershell
cd frontend
npm install
npm run dev:admin
npm run dev:user
```

分别访问：

- 管理端：http://127.0.0.1:5174
- 用户端：http://127.0.0.1:5175

前端通过 Vite 代理访问后端 `/api`，无需额外配置跨域。

生产构建：

```powershell
npm run build
```

产物分别位于 `frontend/dist/admin` 与 `frontend/dist/user`。

## 五、首次使用路径

1. 管理员登录管理端，按提示修改初始密码。
2. 进入「用户与权限」创建普通用户，设置初始密码。
3. 进入「知识库」，选择示例知识库，上传 `resource/示例/` 下的文档。
4. 点击文档行的「入库」，等待索引完成；模型首次下载可能较慢。
5. 进入「问答」，选择知识库与策略，提问并查看引用。
6. 普通用户登录用户端提问，对回答提交反馈。
7. 管理员在「反馈处理」中受理、回复、解决或驳回反馈。
8. 普通用户在「我的反馈」查看处理进度与回复。

## 六、接口概览

所有接口以 `/api/v1` 为前缀，除登录与健康检查外均需登录。

| 分组 | 路径 | 说明 |
| --- | --- | --- |
| 认证 | `/auth/login`、`/auth/logout`、`/auth/me`、`/auth/change-password` | 统一登录与改密 |
| 问答 | `/chat/stream` | SSE 流式问答 |
| 会话 | `/conversations`、`/conversations/{id}/messages` | 会话归属隔离 |
| 知识库 | `/kb`、`/kb/{id}/documents`、`/kb/{id}/rebuild` | 用户只读已发布，写操作仅管理员 |
| 策略 | `/strategies`、`/admin/strategies` | 用户只能读取已开放策略 |
| 反馈 | `/feedback`、`/feedback/mine`、`/admin/feedback` | 用户提交与查看本人反馈，管理员处理 |
| 用户 | `/admin/users` | 管理员创建、改角色、禁用、重置密码 |
| 日志 | `/admin/logs/retrieval`、`/admin/logs/audit` | 仅管理员 |
| 模型 | `/admin/model-config`、`/admin/model-config/ollama-models`、`/admin/model-config/test` | 仅管理员；在线 API 与本地 Ollama 双模式切换，密钥只从环境变量读取 |

错误语义：未登录 401，无权限 403，资源不存在或不属于当前用户 404，状态冲突 409，参数不合法 422，触发限流 429。

## 七、验收与测试

后端冒烟测试覆盖登录、上传入库、混合检索问答、引用返回、反馈提交：

```powershell
backend\.venv\Scripts\python.exe backend\scripts\smoke_test.py 管理员密码
```

关键验收项：

- 未登录访问业务接口返回 401
- 普通用户访问管理接口返回 403
- 普通用户只能看到已发布知识库
- 问答返回引用列表，引用包含文件名与命中片段
- 用户只能读取自己的会话与反馈
- 反馈状态按待处理、处理中、已解决、已驳回流转，并有处理时间线

## 八、常见问题

**问答接口返回 503**

`.env` 未配置 `LLM_API_KEY`，补齐后重启后端。

**索引重建失败**

优先检查模型是否下载完成、网络是否可访问模型仓库、`indexes/` 是否可写。错误信息会写入文档行的错误列与 `logs/backend.log`。

**模型下载超时或很慢**

默认走 `HF_ENDPOINT=https://hf-mirror.com` 镜像。首次索引会下载嵌入模型，首次问答会下载精排模型，精排模型体积较大。可提前把模型复制到 `.hf-cache/hub/` 复用，或把 `RERANK_ENABLED` 设为 `false` 先跳过精排验证其余链路。

**登录提示失败次数过多**

同一用户名与 IP 在 10 分钟内失败 5 次会锁定。可等待锁定时间结束，或清空 Redis 库 4 中对应的 `login:fail:` 键。

**MySQL 连接失败**

确认服务已启动、端口与 `.env` 一致、账号具备建库权限。MySQL 8 使用 `caching_sha2_password`，依赖中的 `cryptography` 必须安装成功。

**前端 401 后不断跳登录页**

检查后端是否正常、令牌是否过期。前端把登录态存放在 `sessionStorage`，关闭标签页后需要重新登录。

## 九、当前阶段边界

- 已实现阶段一：认证、双前端、角色权限、问答、引用、会话归属、反馈闭环、审计与限流。
- 尚未实现：文档级 ACL、文档版本与生效日期、条款级结构化分块、移动端、热点缓存、深度问答循环。
- 认证源扩展预留为本地账号，CAS、企业微信、钉钉尚未接入。
