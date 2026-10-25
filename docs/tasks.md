# 第三版（Agentic）迁移任务清单 Tasks

> 与 `docs/spec.md` 一一对应。全部完成，逐项已验证。

## T1 搭建 agent/backend 骨架（复用基础设施）
- [x] 复制 `product/backend` → `agent/backend`（排除 `.venv/data/indexes/storage/logs/__pycache__/.env`）。
- [x] 核对 `requirements.txt`、`alembic/`、`scripts/`、`src/` 复用文件就位。
- **验证**：`agent/backend/src` 模块树与 product 一致（robocopy /E + 排除项）。

## T2 后端改动：config + llm
- [x] `src/core/config.py` 新增 `AgentSettings` + `AppSettings.agent` 字段。
- [x] `src/generation/llm.py`：`_params()` 加 `tools`；新增 `ChatMessage`；新增 `LLMClient.chat()`。
- **验证**：`python -m src.core.config` 显示 `agent` 分组（max_decision_rounds=3）；`from src.generation.llm import LLMClient, ChatMessage` OK。

## T3 后端新模块：调度层 src/agent/
- [x] `src/agent/__init__.py`
- [x] `src/agent/prompts.py`（AGENT_SYSTEM / TOOL_SCHEMAS / FORCE_ANSWER_INSTRUCTION）
- [x] `src/agent/state.py`（AgentState + 去重 + 证据渲染）
- [x] `src/agent/tools.py`（STRATEGY_SWITCHES / strategy_to_switches / execute_retrieve）
- [x] `src/agent/loop.py`（AgentLoop + AgentResult，MAX_DECISION_ROUNDS=3，三条保底）
- **验证**：`import src.agent.loop, src.agent.tools, src.agent.prompts` OK；`MAX_DECISION_ROUNDS=3`。

## T4 后端接线：schema + service + api
- [x] `src/schemas/agent.py`（AgentChatRequest / AgentChatResponse）
- [x] `src/services/agent_service.py`（agent_chat / prepare_agent_stream / agent_stream_events）
- [x] `src/api/v1/agent.py`（/agent/chat + /agent/chat/stream）
- [x] `src/api/v1/__init__.py` 挂载 agent 路由。
- **验证**：py_compile OK；`import src.services.agent_service, src.api.v1.agent` OK。

## T5 前端 React 脚手架 + 基础设施
- [x] `agent/frontend` 建 Vite+React 工程（package.json/vite.config/index.html/main.jsx）。
- [x] 复制 `theme.css`；`api/request.js`；`api/{kb,chat,agent,document,log}.js`；stores（kb/chat/agent）+ `utils/toast.js`。
- [x] `App.jsx` + `router.jsx`（`/ → /agent`，4 路由：agent/knowledge/strategy/logs）。
- **验证**：`npm run build` 通过（126 modules，初版含问答页）。

## T6 前端共享组件（React 重写）
- [x] `MainLayout.jsx`、`ChatMessage.jsx`、`CitationCard.jsx`、`RetrievalDetail.jsx`、`StrategyBuilder.jsx`
- [x] **`DecisionTrace.jsx`（决策轨迹时间线，参考 RetrievalDetail 视觉）**
- **验证**：`npm run build` 通过、无渲染引用报错。

## T7 前端页面（4 页）
- [x] `pages/AgentChat.jsx`（问答 + 决策轨迹面板 + SSE 实时追加）
- [x] `pages/KnowledgeBase.jsx`、`RetrievalStrategy.jsx`、`Logs.jsx`（React 移植，接口按后端真实路由）
- [x] 移除 `pages/ChatQuery.jsx`，改由「智能问答」承担唯一问答入口。
- **验证**：`npm run build` 通过（125 modules）；历史会话消息从 `latency.agent_trace` 还原时间线；浏览器实测 4 页可导航。

## T8 文档
- [x] `agent/README.md` 重写为独立项目文档（与 `demo/README.md`、`product/README.md` 同构：功能/架构/技术栈/目录/启动/配置/API/轨迹契约/评估口径/限制）。
- [x] `agent/backend/.env.example` 的模型缓存与语料目录改为相对路径（`../../.hf-cache`、`../../product/resource`），换机器免改绝对路径。
- [x] `docs/项目迁移.md`、`docs/spec.md`、`docs/tasks.md` 与最终实现对齐（4 页、`/chat` 已下线、`DecisionTrace` 实际字段）。
- **验证**：文档内路由、目录、字段名与代码逐项核对一致。

## T9 下线固定管道问答（后端）
- [x] `src/api/v1/chat.py` 删除 `POST /chat`、`POST /chat/stream`，仅留会话管理。
- [x] `src/services/chat_service.py` 删除 `chat/prepare_stream/stream_events` 及 6 个检索辅助函数。
- [x] `src/schemas/chat.py` 删除 `ChatRequest/ChatResponse`，同步 `schemas/__init__.py`。
- [x] `tests/test_smoke.py` 路由断言改为 `/agent/chat` 与 `/agent/chat/stream`。
- **验证**：`pytest tests/test_smoke.py` 7 passed；`/openapi.json` 无 `/api/v1/chat`；实跑 `POST /agent/chat` 正常落库并可回读。

## T10 收尾验收
- [x] 走查 spec §5 验收清单：后端语法 / 配置 / 导入 / 前端构建全部通过。
- [x] 真实接口冒烟：闲聊 1 轮直接作答；法条问题 2 轮（5 条引用）；库外问题 3 轮并如实拒答。
- [x] SSE 事件顺序确认：`meta → agent_step → delta → done`。
- **验证**：三档行为与「最多 3 轮 + 第 3 轮强制作答」一致；前端深色模式与决策轨迹面板正常。