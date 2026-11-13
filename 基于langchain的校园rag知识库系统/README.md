# 基于 LangChain 的校园 RAG 知识库系统

面向校园制度问答的独立系统。检索与生成链路基于 LangChain 构建，包含管理端、PC 用户端与移动端三个前端入口、统一身份认证、角色权限、知识库管理、混合检索问答、引用溯源与反馈闭环。移动端是普通用户的主入口，也是企业微信登录的落地页面。阶段二在此基础上补充文档级权限、版本生命周期、条款级结构化分块、评估回归与人工转办。

测试语料为项目根目录的 `测试集.pdf`，属于《普通高等学校学生管理规定》等教育规章汇编，仅用于内部测试，已加入 `.gitignore`，不随仓库分发。

## 一、技术栈

| 层次 | 组件 |
| --- | --- |
| 后端 | FastAPI、Uvicorn、SQLAlchemy 2 异步 ORM、Alembic |
| 数据库 | MySQL 8 |
| 认证 | bcrypt、JWT、服务端可撤销会话表 |
| 解析 | pdfplumber、python-docx，表格转 Markdown |
| 检索 | LangChain、Chroma、BM25（jieba + rank_bm25）、EnsembleRetriever、CrossEncoderReranker |
| 生成 | LCEL 链、ChatOpenAI（兼容 DeepSeek 等 OpenAI 协议服务） |
| 限流 | Redis，不可用时退化为进程内计数 |
| 前端 | Vue 3、Vite、Element Plus、Pinia、Vue Router |

## 二、目录结构

```text
.
├── backend/                 后端服务、迁移、脚本、测试
│   ├── alembic/             数据库迁移
│   ├── scripts/             初始化、健康检查、冒烟测试、备份
│   ├── src/
│   │   ├── api/             路由与鉴权依赖
│   │   ├── core/            配置、安全、日志
│   │   ├── db/              ORM 模型与会话
│   │   ├── ingest/          LangChain 加载、切分、索引流水线
│   │   ├── retrieval/       Chroma、BM25、融合检索、精排、检索范围
│   │   ├── evaluation/      黄金测试集校验、检索与生成评估
│   │   ├── generation/      LCEL 链、提示模板、模型工厂
│   │   ├── services/        认证、问答、文档版本、反馈、设置编排
│   │   ├── schemas/         请求与响应模型
│   │   └── utils/           限流、脱敏
│   └── tests/
├── frontend/                三端前端，同一工程按 VITE_APP_MODE 构建管理端、用户端与移动端
├── resource/法律领域/        参考资料
├── 测试集.pdf               内部测试语料，已加入 .gitignore
├── docs/                    需求、阶段文档、评估与部署说明
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
| 移动端 | 5176 |
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
- `CORS_ORIGINS`：开发环境用 Vite 代理可保持默认；三端分别部署时需包含 5174、5175、5176 或对应的正式域名
- `WECOM_ENABLED` 与企业微信配置：`WECOM_CORP_ID`、`WECOM_AGENT_ID`、`WECOM_SECRET`、`WECOM_REDIRECT_URI` 齐全且开关为 `true` 时，移动端登录页展示企业微信登录；`WECOM_REDIRECT_URI` 需与企业微信后台的网页授权回调域名一致，指向移动端 `/wecom/callback`

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
4. 创建初始管理员，并打印初始密码或提示密码来自环境变量

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
npm run dev:mobile
```

分别访问：

- 管理端：http://127.0.0.1:5174
- PC 用户端：http://127.0.0.1:5175
- 移动端：http://127.0.0.1:5176，手机浏览器或用浏览器移动模拟器访问，企业微信回调也指向该端口

前端通过 Vite 代理访问后端 `/api`，无需额外配置跨域。

生产构建：

```powershell
npm run build
```

产物分别位于 `frontend/dist/admin`、`frontend/dist/user` 与 `frontend/dist/mobile`。

## 五、首次使用路径

1. 管理员登录管理端，按提示修改初始密码。
2. 进入「部门管理」维护部门层级、联系人、排序与启用状态，再进入「用户与权限」创建用户，设置角色（系统管理员、部门管理员、教职工、学生）与所属部门。
3. 进入「知识库」，新建知识库并选择可见性、归属部门、责任人与复核周期。全校公开库仅系统管理员可建；部门管理员只能建本部门内部库。分块策略建议制度类文档使用 `clause`，即条款级切分。
4. 上传文档，填写标题、文号、版本号、可见性、生效日期与复核日期；可见性为「部门内部」时必须选择归属部门。
5. 在版本抽屉中执行「解析」，查看分块统计与一致性检查结果；处理完成后再「发布」，旧版本自动废止。
6. 管理端「问答」验证引用与检索详情；用户端登录后先选择知识库，再提问。学生只能选择公开库，教职工与部门管理员可选公开库与本部门库。
7. 无依据或拒答时可点击「转人工」，生成 `TR-YYYYMMDD-0001` 转办单并自动分配到知识库归属部门。
8. 部门管理员在「反馈处理」受理、回复、办结或退回；用户在「我的反馈」查看处理进度、首次响应时间与回复，并在通知中接收状态变更。
9. 管理员在「评估回归」校验测试集、运行检索或生成评估并查看报告。
10. 管理员在「热点缓存」查看命中率、调整有效期、开启精确或语义缓存、清理缓存与标记疑似误答；在「深度问答」按知识库与角色开启三轮决策。
11. 管理员与部门管理员在「运营看板」查看提问量、零引用率、转办率、缓存命中率、P95、部门对比与文档到期复核；在「知识缺口」汇总零引用、拒答、负反馈与转办问题并导出 CSV。
12. 配置企业微信后，用户在企业微信工作台打开移动端，登录页显示「企业微信登录」，静默授权后按企业微信成员账号匹配同名站内用户名并自动绑定；未匹配到账号时提示联系管理员开通。

角色可见范围：学生只看公开知识库；教职工与部门管理员可访问公开知识库与本部门知识库；系统管理员可管理全部知识库，但正常问答仍只返回当前有效、已发布的文档。「仅管理员」文档不进入任何角色的问答结果，只能在管理端预览。知识库停用或归属部门停用后不再对用户展示，既有会话可读但新问答不可用。

### 移动端适配

- 移动端是独立构建与独立路由，开发地址 5176，产物 `frontend/dist/mobile`，与 PC 用户端共用登录、问答、反馈与通知接口。
- 页面采用移动优先布局：顶部为标题、通知与设置，底部固定「问答」「我的反馈」两个标签，内容区按 `100dvh` 与 `env(safe-area-inset-bottom)` 适配刘海屏与底部手势区。
- 窄屏断点为 768 像素；会话列表改为侧滑抽屉，引用详情改为底部抽屉，检索详情改为全屏覆盖，主要按钮保持不小于 44 像素的触控高度。
- 回答正文在窄屏下为 14 像素、行高 1.6，标题依次为 18、16、15 像素，引用卡片为 13 像素；360 至 430 像素宽度下正文与表格不产生横向滚动，表格超宽时在卡片内横向滚动。
- 支持停止生成、回到底部、切换知识库清空上下文、深度问答与决策轨迹；通知抽屉在移动端全屏展示。
- 企业微信网页授权回调指向移动端 `/wecom/callback`，登录成功后进入移动端问答页；桌面端布局与字号不受移动端样式影响。
- PC 用户端登录页展示移动端二维码，地址优先读取 `VITE_MOBILE_URL`，未配置时用当前主机加 5176；Vite 开发服务监听所有网卡，手机连同一局域网即可扫码访问。
- 移动端构建带 Manifest 与 Service Worker，可添加到主屏幕；首次访问后离线可打开应用外壳，顶部提示离线状态，问答接口不做缓存。

## 六、接口概览

所有接口以 `/api/v1` 为前缀，除登录、企业微信登录与健康检查外均需登录。

| 分组 | 路径 | 说明 |
| --- | --- | --- |
| 认证 | `/auth/login`、`/auth/logout`、`/auth/me`、`/auth/change-password` | 统一登录与改密 |
| 企业微信 | `/auth/wecom/status`、`/auth/wecom/authorize`、`/auth/wecom/callback` | 唯一的外部身份接入方式；未启用时状态接口返回 `enabled=false`，回调校验一次性 state |
| 问答 | `/chat/stream`、`/chat/capabilities` | SSE 流式问答；`mode` 支持 fast 与 deep，检索范围由登录态解析 |
| 会话 | `/conversations`、`/conversations/{id}/messages` | 会话归属隔离 |
| 知识库 | `/knowledge-bases`、`/kb`、`/kb/{id}/documents`、`/kb/{id}/rebuild` | 用户端按角色返回可用库；管理端按部门范围；写操作需文档管理权限 |
| 文档治理 | `/admin/documents`、`/admin/documents/{id}/versions`、`/admin/versions/{id}/parse\|publish\|repeal\|rollback` | 文档与版本生命周期；部门管理员仅限本部门 |
| 文档预览 | `/documents/{id}/preview`、`/admin/documents/{id}/preview` | 按 ACL 校验，越权返回 404 |
| 部门 | `/admin/departments` | 管理员可写，管理员与部门管理员可读；支持层级、排序与停用，管理端「部门管理」页面维护 |
| 策略 | `/strategies`、`/admin/strategies` | 用户只能读取已开放策略 |
| 反馈 | `/feedback`、`/feedback/transfer`、`/feedback/mine`、`/admin/feedback` | 用户提交、转人工与查看本人记录，管理员处理 |
| 通知 | `/notifications`、`/notifications/read-all` | 反馈与转办状态变更、文档复核提醒的站内通知 |
| 缓存 | `/admin/cache/settings`、`/admin/cache/stats`、`/admin/cache/clear`、`/admin/cache/samples` | 仅管理员；精确与语义缓存设置、命中统计、清理与误答标记 |
| 深度问答 | `/admin/agent/settings` | 仅管理员；按知识库与角色开放，最多三轮决策 |
| 运营看板 | `/admin/dashboard/summary`、`/trends`、`/departments` | 管理员看全部，部门管理员看本部门 |
| 知识缺口 | `/admin/gaps`、`/admin/gaps/scan`、`/admin/gaps/export` | 汇总零引用、拒答、负反馈与转办问题 |
| 文档复核 | `/admin/review/due`、`/admin/review/{id}/action`、`/admin/review/notify` | 到期清单、继续有效、发起修订、废止与站内提醒 |
| 评估 | `/admin/evaluation/reports`、`/validate`、`/run` | 管理员运行，管理员与部门管理员查看 |
| 用户 | `/admin/users` | 管理员创建、改角色与部门、禁用、重置密码 |
| 日志 | `/admin/logs/retrieval`、`/admin/logs/audit` | 仅管理员 |
| 模型 | `/admin/model-config`、`/admin/model-config/ollama-models`、`/admin/model-config/test` | 仅管理员；在线 API 与本地 Ollama 双模式切换，密钥只从环境变量读取 |
| 健康检查 | `/health` | 不在 `/api/v1` 前缀下；含数据库与索引一致性 |

错误语义：未登录 401，无权限 403，资源不存在或不属于当前用户 404，版本冲突或重复上传 409，参数不合法 422，触发限流 429，索引未就绪 503。

## 七、验收与测试

后端单元测试：

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
```

覆盖检索范围构造、文档可见性、知识库可见性矩阵、生命周期状态、条款切分、评估指标、引用结构、缓存键与权限隔离、语义相似度计算、深度问答轮数与检索上限、看板口径、企业微信开关与授权链接，共 73 项。

后端冒烟测试覆盖登录、上传入库、混合检索问答、引用返回、反馈提交：

```powershell
backend\.venv\Scripts\python.exe backend\scripts\smoke_test.py 管理员密码
```

评估工具：

```powershell
cd backend
.\.venv\Scripts\python.exe -m src.evaluation.cli validate --testset tests\fixtures\evaluation_sample.jsonl
.\.venv\Scripts\python.exe -m src.evaluation.cli retrieval --testset tests\fixtures\evaluation_sample.jsonl --kb-id 3 --top-k 10
.\.venv\Scripts\python.exe -m src.evaluation.cli generation --testset tests\fixtures\evaluation_sample.jsonl --kb-id 3 --limit 20
```

关键验收项：

- 未登录访问业务接口返回 401
- 无权角色访问管理接口返回 403，越权预览返回 404
- 学生检索不到部门内部与仅管理员文档；教职工检索不到其他部门内部文档
- 同一逻辑文档最多一个当前发布版本，旧版本与未来生效版本不被召回
- 问答返回引用列表，引用包含文件名、页码、条款号、版本号、生效日期与责任部门
- 答案下方固定展示免责声明，无依据时明确拒答并提供转人工入口
- 用户只能读取自己的会话、反馈与转办单
- 反馈与转办按状态流转，转办单记录分配部门、首次响应时间与处理时间线
- `/health` 返回数据库与索引一致性，索引不一致时状态为 degraded
- 学生只能选择公开知识库，教职工与部门管理员只能选择公开库与本部门库，越权选库返回 404
- 精确缓存按知识库、策略、权限范围与索引版本隔离，文档发布、废止、归档、可见性变化后旧键立即失效
- 语义缓存用本地嵌入模型编码问题，在相同知识库、策略、权限范围与索引版本内做余弦相似度匹配，默认关闭，可在「热点缓存」按阈值开启
- 缓存命中后重新校验用户权限、文档当前版本与生效日期，任一不满足时回源
- 深度问答最多三轮决策与三次检索，未检索不得作答，失败时回退快速问答并保留决策轨迹
- 移动端为独立构建，入口为 5176 与 `dist/mobile`，底部导航含问答与我的反馈，企业微信回调落在移动端
- 移动端 360 像素宽度无横向滚动，引用与历史使用抽屉，支持停止生成与回到底部
- PC 用户端登录页提供移动端二维码入口，地址取 `VITE_MOBILE_URL` 或当前局域网主机加 5176
- 移动端构建带 Web App Manifest 与 Service Worker，可添加到主屏幕，首次访问后离线可打开页面并提示联网问答
- 管理端提供独立「部门管理」页面，维护部门层级、联系人、排序与启用状态，后端校验上级部门存在且不形成循环
- 运营看板、知识缺口与文档复核按角色与部门范围统计，导出 CSV 带 BOM 可直接用 Excel 打开
- 反馈与转办状态变更、文档到期提醒写入站内通知，通知失败不影响业务提交

阶段二实施细节、验证记录与未完成项见 [docs/阶段二实施记录.md](docs/阶段二实施记录.md)；阶段三实施细节与验证记录见 [docs/阶段三实施记录.md](docs/阶段三实施记录.md)；评估口径与回归流程见 [docs/evaluation/regression_v2.md](docs/evaluation/regression_v2.md)；备份恢复见 [docs/deployment/backup.md](docs/deployment/backup.md)。

## 八、常见问题

**问答接口返回 503**

`.env` 未配置 `LLM_API_KEY`，补齐后重启后端。

**索引重建失败**

优先检查模型是否下载完成、网络是否可访问模型仓库、`indexes/` 是否可写。错误信息会写入文档行的错误列与 `logs/backend.log`。

**模型下载超时或很慢**

默认走 `HF_ENDPOINT=https://hf-mirror.com` 镜像。首次索引会下载嵌入模型，首次问答会下载精排模型，精排模型体积较大。可提前把模型复制到 `.hf-cache/hub/` 复用，或把 `RERANK_ENABLED` 设为 `false` 先跳过精排验证其余链路。

**登录提示失败次数过多**

同一用户名与 IP 在 10 分钟内失败 5 次会锁定。可等待锁定时间结束，或清空 Redis 库 4 中对应的 `login:fail:` 键。

**登录页没有企业微信登录按钮**

确认 `.env` 中 `WECOM_ENABLED=true`，且 `WECOM_CORP_ID`、`WECOM_AGENT_ID`、`WECOM_SECRET`、`WECOM_REDIRECT_URI` 全部填写后重启后端。`GET /api/v1/auth/wecom/status` 返回 `enabled=true` 时才会展示按钮。回调地址需与企业微信后台的可信域名一致并指向移动端 `/wecom/callback`；登录 state 存放在 Redis 库 4，10 分钟内有效。

**企业微信登录提示未绑定系统账号**

企业微信成员账号需与站内用户名一致。管理员先在「用户与权限」创建同名用户，首次授权后自动写入绑定关系，后续按绑定关系登录；换绑需先删除 `auth_identities` 中对应记录。

**缓存命中率始终为 0**

先确认 `REDIS_URL` 可连接，再确认问答来自新会话首问且模式为快速问答。多轮追问、深度问答、拒答与转人工回答按设计不进入缓存。文档发布或可见性变更后索引版本递增，旧缓存不会命中。

**语义缓存没有命中**

语义缓存默认关闭，先在「热点缓存」开启并确认阈值，默认 0.95。只有问题表述不同但语义接近时才走语义匹配；检索详情会显示「语义相似」百分比，精确命中的显示为「精确命中」。阈值过高容易不命中，过低可能引入误答，建议用真实问题抽样后再调低。

**MySQL 连接失败**

确认服务已启动、端口与 `.env` 一致、账号具备建库权限。MySQL 8 使用 `caching_sha2_password`，依赖中的 `cryptography` 必须安装成功。

**前端 401 后不断跳登录页**

检查后端是否正常、令牌是否过期。前端把登录态存放在 `sessionStorage`，关闭标签页后需要重新登录。

## 九、当前阶段边界

- 已实现阶段一：认证、双前端、角色权限、问答、引用、会话归属、反馈闭环、审计与限流。
- 已实现阶段二：文档级 ACL、文档版本与生效日期、条款级结构化分块与父子块、引用版本化、黄金测试集评估模块、人工转办、索引一致性检查与备份脚本。
- 已实现阶段三：多部门治理与知识库归属、独立移动端前端、精确与语义热点缓存、独立部门管理页面、三轮深度问答、站内通知、运营看板、知识缺口与到期复核、企业微信登录接口、移动端二维码入口与 PWA 离线能力。
- 尚未完成：真实 300 至 500 条黄金测试集与正式基线、多部门真实数据与跨部门联合检索、企业微信真实凭证联调、GPU 精排压测、备份恢复演练、邮件短信通知、真机与多设备浏览器验收。上述内容需在学校服务器与真实凭证就绪后完成。
- 外部身份只保留企业微信网页授权一种接口，采用静默授权获取成员账号并按同名站内账号自动绑定；未配置企业微信时仅使用本地账号登录。
