# 第三版（Agentic）迁移实现规格 Spec

> 依据 `docs/项目迁移.md` 定稿的执行级规格：锁定每个文件的改动、函数签名、数据契约与验收方式。
> 设计文档为背景，本文档为**执行口径**；两者冲突时以本文为准。

## 0. 范围与三大硬约束

- 在三版之外新增 `agent/`（后端 + 前端），三版索引/数据/配置相互独立；`product/` 与 `demo/` 不改动。
- 约束 1：单问题 Agent 最多 3 轮决策，第 3 轮强制收敛（`MAX_DECISION_ROUNDS=3`）。
- 约束 2：评估模块具体（见 `docs/项目迁移.md` §10，本次实现不新增评估代码，仅保留指标口径）。
- 约束 3：复用现有检索/生成/引用/会话基础设施；新增代码仅限「调度层 + API + schema + 前端」。
- 前端只保留**智能问答**入口：固定管道问答页（`ChatQuery`）不保留；后端同步下线 `POST /chat`、`POST /chat/stream`。

## 1. 关键设计决策（已定稿）

| 决策点 | 结论 |
| --- | --- |
| 最终答案生成 | **方案 A**：Agent 不调工具时直接输出的 `content` 即最终答案，不再经 `PromptBuilder.generate` |
| 检索策略 | Agent 逐轮改写 query + 四选一 `vector/bm25/hybrid/hybrid_rerank` → 映射为现有四开关 |
| 证据编号 | 累积证据用 `contexts_from_results(..., start_index=1)` + `PromptContext.render()` 渲染，跨轮编号连续且去重（按 `chunk_id`） |
| 引用解析 | `citation.extract_citations(answer, contexts_from_results(state.evidences))` 零改动复用 |
| 落地方式 | `agent/backend` 复制自 `product/backend`（排除 `.venv/data/indexes/storage/logs/__pycache__/.env`），增量加新模块 |
| 后端 LLM 改动 | `llm.py` 的 `_params()` 加 `tools` 透传；新增 `ChatMessage` dataclass 与 `LLMClient.chat()`（Function Calling 单次调用） |
| 前端技术栈 | React 18 + React Router v6 + Zustand + axios + marked + Vite；**复用 `theme.css`（CSS 变量 + `html.dark`）+ 手写轻量组件**（不引入 Ant Design，保证与现有产品视觉像素级一致） |
| 前端默认路由 | `/` 重定向到 `/agent`（第三版首页即智能问答页）；菜单共 4 项（智能问答 / 知识库 / 检索策略 / 日志），固定管道问答页不保留 |

## 2. 后端目录与文件

### 2.1 复制（基础步骤）
`product/backend` → `agent/backend`（排除项见 §1）。`src/` 下除下列「新增/修改」外全部复用。

### 2.2 修改：`src/core/config.py`
新增 `AgentSettings(_Section)`，并在 `AppSettings` 追加字段 `agent: AgentSettings`：

```
max_decision_rounds: int = 3   # ge=1, le=5
model: str = "deepseek-chat"
temperature: float = 0.0       # ge=0, le=2
max_tokens: int = 1024
timeout: int = 60
max_retrieve_calls: int = 3
evidences_max: int = 20
```

### 2.3 修改：`src/generation/llm.py`
- `_params(...)` 增加关键字 `tools: list | None = None`，返回 dict 在 `tools` 非空时加 `"tools": tools`（否则不出现该键，避免旧路径报错）。
- 新增 `@dataclass ChatMessage`：`content: str = ""`、`tool_calls: list = []`（每个元素 `{"id","name","arguments"}`，arguments 为 JSON 字符串）、`usage: dict`、`finish_reason: str = ""`、`model: str = ""`。
- 新增方法 `LLMClient.chat(messages, *, tools=None, model=None, temperature=None, max_tokens=None) -> ChatMessage`：调用 `self._call_with_retry`，解析 `choices[0].message.content/.tool_calls`，返回 `ChatMessage`。

### 2.4 新增：`src/agent/__init__.py`
导出 `AgentLoop`、`AgentResult`、`MAX_DECISION_ROUNDS`。

### 2.5 新增：`src/agent/prompts.py`
- `AGENT_SYSTEM`（见 `项目迁移.md` §9.1 全文）。
- `TOOL_SCHEMAS`（见 §9.2：`retrieve`、`ask_clarification` 两份 JSON Schema）。
- `FORCE_ANSWER_INSTRUCTION = "已达决策轮次上限，请立即直接输出最终回答，禁止再调用任何工具。"`

### 2.6 新增：`src/agent/state.py`
`@dataclass AgentState`：
- 字段：`question`、`kb_id`、`history(list[(role,content)])`、`messages(list)`（OpenAI 消息列表，跨轮复用）、`evidences(list[RetrievalResult])`、`_seen(set[str])`、`trace(list)`、`rounds`。
- `build_initial()`：`messages = [system(AGENT_SYSTEM)] + history(最近 N 轮，role∈{user,assistant}) + [user(question)]`。
- `add_evidences(results) -> list[RetrievalResult]`：按 `chunk_id` 去重后追加，返回本轮新增子序列。
- `evidence_contexts() -> list[PromptContext]`：`contexts_from_results(self.evidences, start_index=1)`。
- `append_turn(assistant_msg, tool_result_msg)`：把两则消息追加进 `messages`。

### 2.7 新增：`src/agent/tools.py`
- `STRATEGY_SWITCHES`：四档映射（见 `项目迁移.md` §4.3 表）。
- `strategy_to_switches(name) -> dict[str,bool]`。
- `execute_retrieve(state, query, strategy) -> dict`：
  - `from src.services.retrieval_service import retrieve` → `resp = retrieve(state.kb_id, query, strategy_to_switches(strategy))`。
  - `new = state.add_evidences(resp.results)`；计算 `start_index = len(state.evidences) - len(new) + 1`。
  - `rendered = "\n\n".join(c.render() for c in contexts_from_results(new, start_index))`。
  - 返回 `{"query","strategy":resp.strategy,"result_count":len(resp.results),"new_count":len(new),"rendered","docs":[扁平 doc ...],"stats":resp.stats,"elapsed_ms"}`。
- 扁平 doc 结构（复用 chat_service `_doc_to_client` 同款字段，直接内联 5 行字典构造）：`chunk_id/rank/text/score/fused_score/rerank_score/matched_by/source/page`。

### 2.8 新增：`src/agent/loop.py`
- 常量 `MAX_DECISION_ROUNDS = settings.agent.max_decision_rounds`（默认 3）。
- `@dataclass AgentResult`：`answer`、`citations(list)`、`retrieved_docs(list)`、`trace(list)`、`rounds(int)`、`latency(dict)`、`is_clarification(bool=False)`、`clarification_question(str|None)`。
- `class AgentLoop`：
  - `__init__(self, llm_client=get_llm_client())`。
  - `run(question, kb_id, history) -> AgentResult`：循环 `round_no in 1..max_decision_rounds`，每轮：
    1. `chat(messages, tools=TOOL_SCHEMAS, model/temperature/max_tokens 取 settings.agent)`。
    2. 若 `tool_calls` 为空 → `_finish(state, content, trace_step)`。
    3. 取第一个 tool_call（约定每轮单工具）；`ask_clarification` → `_clarify(...)`；`retrieve` → `execute_retrieve`，把 assistant(tool_calls) 与 tool(result) 消息 append 进 `messages`，记 trace，`continue`。
    4. 未知工具或超 `max_retrieve_calls` → 直接 `_finish` 兜底。
  - 触顶仍未收敛 → 追加 `FORCE_ANSWER_INSTRUCTION` 的 user 消息再 `chat` 一次，拿 `content` 收尾。
  - `_finish`：`citations = extract_citations(answer, state.evidence_contexts()).citations` 的 `to_dict()["citations"]`；`retrieved_docs` 为全部证据扁平化；`trace` 为每轮一条。
- trace 元素结构（前后端契约）：
  ```
  {"round": 1, "action": "answer|retrieve|ask_clarification|force_answer",
   "tool": "retrieve|ask_clarification|null",
   "args": {"query":str,"strategy":str}|{"question":str}|null,
   "result_count": int, "docs": [扁平doc], "strategy": str, "stats": dict|null,
   "llm_usage": dict, "elapsed_ms": int}
  ```

### 2.9 新增：`src/schemas/agent.py`
- `AgentChatRequest(BaseModel)`：`kb_id:int`、`conversation_id:int|None`、`question:str=""`、`image:str|None`（**无 strategy**），`model_validator` 沿用原固定管道请求的校验（question/image 至少一个、image 为 data URL）。
- `AgentChatResponse(BaseModel)`：`answer:str`、`citations:list`、`retrieved_docs:list`、`latency:dict`、`agent_trace:list`、`rounds:int`、`is_clarification:bool=False`、`clarification_question:str|None=None`、`conversation_id:int`。

### 2.10 新增：`src/services/agent_service.py`
复用 `chat_service` 的 `create_conversation/_recent_history/_save_message/sse`：
- `agent_chat(db, kb, question, *, conversation_id=None, image=None) -> dict`（非流式）：
  - 建会话/存用户消息 → `history` → `run_in_threadpool(AgentLoop().run, text, kb.id, history)` → 存 assistant 消息（`latency` 内嵌 `agent_trace` 与 `rounds`）→ 返回 `AgentChatResponse` 结构。
- `prepare_agent_stream(db, kb, question, *, conversation_id=None, image=None) -> dict`：建会话/存用户消息/取 history/线程池执行 `AgentLoop().run` 得 `AgentResult`，返回 bundle。
- `agent_stream_events(bundle) -> AsyncIterator[str]`：依次 `yield sse({"type":"meta", conversation_id, agent:True})` → 每个 trace 一条 `yield sse({"type":"agent_step","step":{...}})` → `yield sse({"type":"delta","delta":answer})` → `yield sse({"type":"done","response":{...}})`，并在 done 前用独立会话落库 assistant 消息。
- 说明：agent 多轮 LLM 决策为顺序阻塞，答题文不逐 token 流式（与「方案 A 单次产出」一致），SSE 主要承载「决策步骤时间线 + 最终答案」。

### 2.11 新增：`src/api/v1/agent.py`
- `router = APIRouter(tags=["Agent 问答"])`，前缀由 `api_router` 挂载（即 `/api/v1/agent`）。
- `POST /agent/chat`（response_model=AgentChatResponse）。
- `POST /agent/chat/stream`（SSE）。
- 复用 `chat.py` 的 `_resolve_kb` 思路（内联 `_resolve_kb`）。

### 2.12 修改：`src/api/v1/__init__.py`
`from src.api.v1 import agent` 并 `api_router.include_router(agent.router)`。

### 2.13 修改：下线固定管道问答（原 product `/chat`）
- `src/api/v1/chat.py`：删除 `POST /chat`、`POST /chat/stream`，仅保留会话管理；路由标签改为「会话」。
- `src/services/chat_service.py`：删除 `chat()`、`prepare_stream()`、`stream_events()` 及其检索/耗时/引用辅助函数；保留会话 CRUD、`_recent_history`、`_save_message`、`sse`（Agent 门面复用）。
- `src/schemas/chat.py`：删除 `ChatRequest`、`ChatResponse`；保留 `ConversationOut`、`MessageOut`，并同步 `schemas/__init__.py` 导出。
- 目的：本版问答只有 Agent 一条链路，避免保留「策略由前端传入」的旧语义。

## 3. 前端目录与文件（React + Vite）

### 3.1 脚手架
- `package.json`：deps `react@18/react-dom@18/react-router-dom@6/zustand@4/axios/marked`；devDeps `vite@5/@vitejs/plugin-react`。
- `vite.config.js`：`@vitejs/plugin-react`、alias `@ -> ./src`、server.proxy `/api -> http://localhost:8000`。
- `index.html`、`src/main.jsx`（ReactDOM.createRoot，应用前按 `localStorage['app-theme']==='dark'` 给 `<html>` 加 `dark`）。
- `src/assets/theme.css`：**完整复制** `product/frontend/src/assets/theme.css`。

### 3.2 基础
- `src/App.jsx`：`<BrowserRouter><Routes>...</Routes></BrowserRouter>`。
- `src/router.jsx`：路由 `agent/knowledge/strategy/logs`（4 条），`/ → /agent`，通配重定向 `/agent`；`document.title` 按 `meta.title` 设置。
- `src/api/request.js`：axios 实例（baseURL `/api/v1`，拦截器解包 + toast 提示）。
- `src/api/kb.js`、`src/api/chat.js`（会话列表/消息、`parseSSE`）、`src/api/agent.js`（`agentChat`/`agentChatStream` 用 fetch+ReadableStream 解析 SSE，扩展 `agent_step` 事件回调用）。
- `src/stores/kbStore.js`、`src/stores/chatStore.js`、`src/stores/agentStore.js`（Zustand）。

### 3.3 共享组件（React 重写，复用 theme.css 语义）
- `components/MainLayout.jsx`：侧边栏（品牌 + 菜单 4 项 + 深色开关）+ 顶栏（折叠键 + 标题）+ `<Outlet/>`。
- `components/ChatMessage.jsx`：`marked` 渲染 markdown，`[1][2,3][1-3]` 引用编号转可点击 `span.cite-ref`（onClick 冒泡触发 onCite）。
- `components/CitationCard.jsx`：`[n] 来源 · 第 p 页 · 相关度 score · 引用 count 次` + 正文。
- `components/RetrievalDetail.jsx`：文档块列表（rank/source/page + 最终/融合/精排/命中分 + 文本预览）+ 耗时分解 + 两路召回统计（忠实对应 `RetrievalDetail.vue`）。
- `components/StrategyBuilder.jsx`：readonly 四标签（向量/BM25/融合/精排 + `→ 组合名`）。
- **`components/DecisionTrace.jsx`（本次新增核心，参考 RetrievalDetail 视觉）**：时间线渲染 `agent_trace`——每轮一张卡：轮次徽标 + 行动标签（直接作答/检索/澄清/强制作答）+ `retrieve` 时展示「改写 query、策略组合标签、命中条数、本次召回文档块列表（复用检索详情同款 doc 行）、耗时」；`answer/ask_clarification` 展示说明文本；顶部汇总「共 N 轮 · 检索 M 次 · 累计召回 K 条」。

### 3.4 页面
- `pages/AgentChat.jsx`（新增，落地 `/agent`）：知识库选择 + 会话列表 + 消息流（复用 ChatMessage/CitationCard）+ 右侧「决策轨迹」面板（复用 DecisionTrace，默认展开）+ 底部输入框；接入 `agentChatStream`（`onStep` 实时追加 DecisionTrace，`onDelta/onMeta/onDone` 渲染答案与引用）。
- `pages/KnowledgeBase.jsx`、`pages/RetrievalStrategy.jsx`、`pages/Logs.jsx`：由对应 Vue 视图忠实移植（复用现成 `api/kb.js`、`api/document.js`、`api/log.js`）。
- 不保留 `pages/ChatQuery.jsx`：本版只有智能问答一条问答入口。

## 4. 数据契约（前后端一致）

- 非流式 `/agent/chat` 响应 = `AgentChatResponse`（含 `agent_trace`）。
- 流式 `/agent/chat/stream` 事件顺序：`meta` → `agent_step`×N → `delta` → `done`（done.response 含 `answer/citations/retrieved_docs/latency/agent_trace/rounds/is_clarification`）。
- 历史会话回看：`Messages.latency.agent_trace`（`list_messages` 已透传 latency），前端从 `msg.latency?.agent_trace` 读取渲染 DecisionTrace。

## 5. 验收（Verify）

1. 后端语法：`uv run python -c "import src.agent.loop, src.services.agent_service, src.api.v1.agent"`（在 `agent/backend`）。
2. 配置：`python -m src.core.config` 输出含 `agent` 分组且 `max_decision_rounds=3`。
3. 启动：`python -m src.api.main`，`/docs` 出现 `POST /api/v1/agent/chat` 与 `/agent/chat/stream`。
4. 冒烟（闲聊不需真实 Key 也能走通？否，仅路由存在性验证）：`curl -X POST /api/v1/agent/chat` 传入不存在 kb 返回 404；传入有效 kb + 「你好」→ 走 Agent（需 RAG_LLM__API_KEY）。
5. 前端：`npm install && npm run dev`，`/agent` 可发问，DecisionTrace 时间线逐轮渲染，深色/浅色切换正常，四页（智能问答/知识库/检索策略/日志）可导航并构建通过（`npm run build`）。
6. 回看：历史会话消息的 `latency.agent_trace` 还原时间线。
7. 冒烟测试：`pytest tests/test_smoke.py` 全绿，路由断言为 `/agent/chat`、`/agent/chat/stream`。
8. 旧接口下线：`/openapi.json` 中不存在 `/api/v1/chat`、`/api/v1/chat/stream`，仅保留 4 个 `/conversations` 接口。

## 6. 范围外（本次不实现）

- 评估代码/测试集脚本（口径见 `项目迁移.md` §10，仅文档）。
- 独立 `GET /agent/trace/{id}` 端点（轨迹已内嵌 `latency.agent_trace`，按需后续加）。
- 图片多模态走 Agent（`image` 字段保留但首版仅透传不启用视觉决策）。
- 真实 API Key、MySQL/MinIO/Celery 部署（沿用 product 启动方式，不在本规格内执行）。