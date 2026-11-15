# Linux 部署手册

面向校内正式部署（Ubuntu 22.04 / 24.04）。Windows 开发环境见仓库根目录的 README。

## 0. 部署形态

```
浏览器 ──HTTPS──> Nginx ──┬── /            静态文件 dist/admin|user|mobile
                          └── /api/  ──> uvicorn 127.0.0.1:8004 ──> MySQL 8 / Redis 7
                                                              └──> 模型服务（vLLM / 在线 API / Ollama）
```

- 后端**必须单 worker**：Chroma 使用本地文件索引，同一时间只能被一个进程打开，多进程会直接报错。
- 模型来源三选一，正式上线建议 vLLM，详见 [`GPU加速部署方案.md`](../GPU加速部署方案.md)。
- 目录约定：代码放 `/opt/campus-rag/releases/<日期>`，用 `/opt/campus-rag/current` 软链指向当前版本，便于回滚。

## 1. 前置检查

| 项目 | 要求 | 检查命令 |
|---|---|---|
| 系统 | Ubuntu 22.04 / 24.04 | `lsb_release -a` |
| Python | 3.11（3.10–3.12 均可） | `python3.11 -V` |
| Node.js | 18 及以上 | `node -v` |
| MySQL | 8.0+ | `mysql --version` |
| Redis | 7.0+ | `redis-server -v` |
| Nginx | 任意稳定版 | `nginx -v` |
| 域名 | 三个三级域名（管理端/用户端/移动端） | — |
| GPU | 可选，vLLM 需要 | `nvidia-smi` |

## 2. 系统用户与代码目录

```bash
sudo adduser --system --group --home /opt/campus-rag campus
sudo mkdir -p /opt/campus-rag/releases
sudo chown -R campus:campus /opt/campus-rag

sudo -u campus git clone https://github.com/Huise1102/Rerank_RAG.git \
  /opt/campus-rag/releases/$(date +%Y%m%d)
sudo -u campus ln -sfn /opt/campus-rag/releases/$(date +%Y%m%d) /opt/campus-rag/current
```

后续所有命令中的路径都以 `/opt/campus-rag/current` 为准，实际目录是 `基于LangChain的校园RAG知识库系统`，下文用 `$APP` 代指：

```bash
export APP=/opt/campus-rag/current/基于LangChain的校园RAG知识库系统
```

## 3. MySQL 与 Redis

**方式一：使用项目自带 compose（推荐试用）**

```bash
cd $APP
sudo -u campus docker compose up -d mysql redis
```

compose 中 MySQL 映射到 **3308**、Redis 映射到 **6380**，`.env` 要按这个端口填。

**方式二：使用学校已有实例**

```sql
CREATE DATABASE campus_rag DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'campus_rag'@'%' IDENTIFIED BY '强密码';
GRANT ALL PRIVILEGES ON campus_rag.* TO 'campus_rag'@'%';
FLUSH PRIVILEGES;
```

## 4. 后端依赖与配置

```bash
cd $APP/backend
sudo -u campus python3.11 -m venv .venv
sudo -u campus .venv/bin/pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
sudo -u campus .venv/bin/pip install -r requirements.txt

sudo -u campus cp $APP/.env.example $APP/.env
sudo -u campus chmod 600 $APP/.env
```

`.env` 位于**项目根目录**（`$APP/.env`），不是 `backend/` 下。必填项：

| 变量 | 说明 |
|---|---|
| `SECRET_KEY` | 用 `openssl rand -hex 32` 生成，不能沿用示例值 |
| `MYSQL_HOST/PORT/USER/PASSWORD/DATABASE` | 与第 3 步一致（compose 端口是 3308） |
| `REDIS_HOST/PORT/PASSWORD` | compose 端口是 6380 |
| `ADMIN_USERNAME/ADMIN_PASSWORD` | 首次初始化的管理员，登录后强制改密 |
| `CORS_ORIGINS` | 填三个前端域名，逗号分隔，不能留 `*` |
| `LLM_PROVIDER` | `vllm`（上线）/ `api`（在线）/ `ollama`（调试） |
| `VLLM_BASE_URL/VLLM_MODEL` | 选择 vLLM 时填写，如 `http://127.0.0.1:8001/v1` |
| `WECOM_CORP_ID/AGENT_ID/SECRET/REDIRECT_URI` | 使用企业微信入口时必填，回调需公网 HTTPS |
| `CONTENT_SECURITY_POLICY` 相关 | 按学校要求调整，默认已可用 |

`EMBEDDING_DEVICE` 保持 `cpu` 即可，有 GPU 再改 `cuda`；`RERANK_INPUT_K=12` 是实测过的取值。

## 5. 首次初始化

```bash
cd $APP/backend
sudo -u campus .venv/bin/python scripts/init_db.py       # 建库、迁移、默认检索策略、管理员
sudo -u campus .venv/bin/python scripts/health_check.py  # 数据库、索引一致性自检
```

首次启动会下载向量模型与精排模型（约 1 GB），如网络受限，先设置：

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

`scripts/smoke_test.py` 依赖仓库外的 `测试语料/` 目录（已被 `.gitignore` 排除），服务器上不存在，可跳过。

## 6. 前端构建

```bash
cd $APP/frontend
sudo -u campus npm ci
sudo -u campus npm run build     # 产物：dist/admin、dist/user、dist/mobile
```

构建前确认 `frontend/.env`（或 `.env.production`）中 `VITE_API_BASE_URL=/api/v1`，这是相对路径，由 Nginx 反代，不要写成 `http://ip:8004`。

## 7. 后端常驻（systemd）

```bash
sudo cp $APP/docs/deployment/systemd/campus-rag-backend.service /etc/systemd/system/
sudo sed -i "s#/opt/campus-rag/current/backend#/opt/campus-rag/current/基于LangChain的校园RAG知识库系统/backend#" \
  /etc/systemd/system/campus-rag-backend.service
sudo systemctl daemon-reload
sudo systemctl enable --now campus-rag-backend
sudo systemctl status campus-rag-backend
```

查看日志与自检：

```bash
sudo journalctl -u campus-rag-backend -f
curl http://127.0.0.1:8004/health
```

单元文件中**没有** `--workers`，请不要自行添加。

## 8. Nginx 与 HTTPS

```bash
sudo cp $APP/docs/deployment/nginx/campus-rag.conf /etc/nginx/sites-available/campus-rag.conf
sudo sed -i 's#/opt/campus-rag/current/frontend#/opt/campus-rag/current/基于LangChain的校园RAG知识库系统/frontend#g' \
  /etc/nginx/sites-available/campus-rag.conf
# 修改 server_name 为实际域名后启用
sudo ln -s /etc/nginx/sites-available/campus-rag.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d admin.example.edu.cn -d ask.example.edu.cn -d m.example.edu.cn
```

配置里已包含两项容易踩坑的设置：`client_max_body_size 25m`（对应 `UPLOAD_MAX_MB=20`）和 `proxy_buffering off`（流式回答必需）。

防火墙只放行 80/443，8004 保持仅本机监听：

```bash
sudo ufw allow 80,443/tcp
```

## 9. 业务侧初始化（容易漏）

部署完接口能通，但知识库还是空的，需要人工完成：

1. 用 `ADMIN_USERNAME/ADMIN_PASSWORD` 登录管理端，按提示修改密码；
2. 「知识库管理」新建知识库；
3. 「文档管理」上传 PDF/DOCX → 解析 → 发布（原始文件直接上传，无需预处理）；
4. 「检索策略」确认召回条数（**预设优先于 `.env`**，改 `.env` 不生效）；
5. 「模型配置」选择来源并点「测试连接」；
6. 「智能体设置」按需开启深度问答，指定角色与知识库白名单；
7. 使用企业微信入口时，在企微后台把可信域名与回调地址指向移动端域名。

## 10. 升级与回滚

```bash
# 升级：拉取新代码后
sudo -u campus $APP/backend/.venv/bin/pip install -r $APP/backend/requirements.txt
sudo -u campus $APP/backend/.venv/bin/python $APP/backend/scripts/init_db.py   # 内含 alembic upgrade head
sudo -u campus npm --prefix $APP/frontend ci && sudo -u campus npm --prefix $APP/frontend run build
sudo systemctl restart campus-rag-backend

# 回滚：把 current 软链指回上一个 release 目录后重启
sudo -u campus ln -sfn /opt/campus-rag/releases/<旧版本目录> /opt/campus-rag/current
sudo systemctl restart campus-rag-backend
```

数据备份（MySQL + 文件）：

```bash
mysqldump -h127.0.0.1 -P3308 -ucampus_rag -p --single-transaction campus_rag | gzip > campus_rag_$(date +%F).sql.gz
tar -czf campus_files_$(date +%F).tar.gz -C $APP storage indexes .env
```

`storage/`（原始文件）、`indexes/`（向量与 BM25 索引）与 `.env` 都不在 Git 中，必须单独备份。

## 11. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 502 Bad Gateway | 后端未启动或端口不符 | `systemctl status`、看 `journalctl` |
| 页面空白 | dist 路径错或未构建 | 检查 Nginx `root` 指向 `dist/admin` 等 |
| `Cannot open header file` / 索引锁 | 多进程同时打开 Chroma | 确认只有一个 uvicorn 进程 |
| 入库失败、解析报权限 | 目录不可写 | `chown -R campus:campus $APP/{storage,indexes,logs,data}` |
| 上传 413 | Nginx 限制过小 | 调 `client_max_body_size`，与 `UPLOAD_MAX_MB` 对齐 |
| 模型下载失败 | 无外网 | 设置 `HF_ENDPOINT` 或用离线模型目录 |
| 登录 429 | 失败次数限流 | 等待窗口结束，或调整 `LOGIN_*` 参数 |
| 回答很慢 | 检索与生成在 CPU | 见 GPU 加速部署方案 |

## 12. Docker 方式（可选）

不使用 systemd 时，可用镜像运行后端，仍然要求**单实例**：

```bash
cd $APP
sudo docker build -t campus-rag-backend ./backend
sudo docker run -d --name campus-rag-backend --restart unless-stopped \
  --env-file .env -p 127.0.0.1:8004:8004 \
  -v $PWD/storage:/app/storage \
  -v $PWD/indexes:/app/indexes \
  -v $PWD/logs:/app/logs \
  -v $APP/.hf-cache:/app/.hf-cache \
  campus-rag-backend
```

镜像内已创建非 root 用户 `campus`（UID 1000），挂载目录需保证该 UID 可写；不要用 `--scale` 起多副本。
