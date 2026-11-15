# GPU 加速部署方案

面向校内正式部署，目标是把本地问答的单次响应从「分钟级」压到「秒级」，同时给出算力选型、推理框架选型、配置调优和验收方法。

---

## 一、现状基线（实测，CPU）

测试环境：qwen3:8b（Ollama，纯 CPU）、bge-small-zh-v1.5 + bge-reranker-v2-m3（CPU），知识库为「学生管理规定」（917 块）。

| 指标 | 实测值 | 说明 |
| --- | --- | --- |
| 首字延迟 | 47.7 s | 用户点下发送到出现第一个字 |
| 整段完成 | 60.8 s | 851 字回答 |
| 其中检索 | 21.3 s | 向量召回 50 + BM25 召回 50 → 精排 5 |
| 其中生成 | ≈ 39 s | 约 15 tokens/s，CPU 跑 8B 的正常水平 |

结论：**瓶颈约 1/3 在检索链路，2/3 在生成**。只升级模型、不搬检索，最多只能解决 2/3；反之亦然。四项改造必须成套做。

---

## 二、改造点 1：显卡选型

### 2.1 显存估算方法

LLM 显存 ≈ 权重 + KV Cache + 框架开销。

- 权重：`参数量 × 量化位宽 / 8`。14B 的 4bit（AWQ/GPTQ）约 9–10 GB，32B 约 19–20 GB。
- KV Cache：与上下文长度、并发数成正比。8K 上下文下，每路并发大约 0.5–2 GB。
- 框架开销：vLLM 预留 10%–20%。

### 2.2 推荐配置

| 场景 | 显卡 | 可跑模型 | 并发能力（估） | 适用 |
| --- | --- | --- | --- | --- |
| 试点 / 200 人 | 1× 24GB（RTX 4090 / L4 / A10） | Qwen2.5-14B-Instruct-AWQ | 15–30 并发 | 先跑通，成本最低 |
| 正式 / 1000 人 | 1× 48GB（L40S / A6000 Ada） | Qwen2.5-32B-Instruct-AWQ 或 14B | 24–35（32B）/ 40+（14B） | 推荐，单卡即可 |
| 高可用 / 2000+ 人 | 2× 48GB 或 4× 24GB | 32B 双副本 + 负载均衡 | 80–120 并发 | 考试周、迎新季 |

选型原则：

- **优先大显存单卡**而不是多张小卡。14B 用 2×24GB 做张量并行，通信开销会吃掉收益。
- 学校机房若已有 L4/A10，先跑 14B，够用就不要上 32B——参数量翻倍，单请求速度反而下降。
- 显存要同时容纳 LLM、embedding（约 0.5 GB）和 reranker（约 2.5 GB），选 24GB 卡时给它们留 4 GB。

### 2.3 显存会不会被占满

不会"占满导致崩溃"，但会被**预占到设定上限**，这是 vLLM 的正常机制：它启动时就把 `--gpu-memory-utilization` 比例内的剩余显存全部划成 KV Cache 池，`nvidia-smi` 看起来接近满，属于预期行为。真正会 OOM 的只有一种情况：权重 + 最小 KV 需求超过上限。

以 48GB 卡跑 32B AWQ 为例：

| 项目 | 占用 | 说明 |
| --- | --- | --- |
| 32B 权重（4bit） | 约 18 GB | 含量化 scale |
| 激活与框架开销 | 2–3 GB | vLLM 运行时 |
| embedding + reranker（同卡） | 约 3 GB | 独立进程，不受 vLLM 管理 |
| KV Cache 池 | 剩余部分 | 每 token 约 256 KB（64 层 / 8 KV 头 / fp16） |
| 单请求满 8K 上下文 | 约 2 GB | 8192 tokens × 256 KB |
| 单请求典型用量 | 约 0.6–0.8 GB | 上下文 3500 字 + 回答 800 字 |

按 `--gpu-memory-utilization 0.85` 算，vLLM 可用 40.8 GB，扣掉权重后 KV 池约 22 GB；但这 3 GB 的 embedding/reranker 在**另一个进程**里，vLLM 并不知道。两者相加约 46 GB，再叠加驱动与碎片，就会顶到 48 GB 上限，偶发 OOM。

因此同卡部署时把上限压到 0.75：

- vLLM 可用约 36 GB，扣权重后 KV 池约 18 GB（约 7 万 tokens 容量）；
- 按单请求平均 2500 tokens 算，可稳定支撑 **24–28 路并发**；
- 给 embedding/reranker 留足余量，不会互相挤占。

如果 32B 要跑到 50 路以上并发，就不要和 rerank 挤一张卡：把 embedding/rerank 放第二张卡，或退到 CPU（检索会慢回 15–20 秒，需权衡）。

### 2.4 1000 人的并发怎么估

注册用户数 ≠ 并发数。校内场景的经验值：

- 日活约 20%–30%，即 200–300 人；
- 同时在线约 5%–8%，即 50–80 人；
- 真正同时在等回答的，**20–50 路**。

按每路输出 800 tokens、平均 8 秒答完算，总吞吐需求约 2000–5000 tokens/s。单张 L40S 跑 32B AWQ 大约 1500–2500 tokens/s，因此正式部署建议 1 张 48GB 起步 + 保留在线 API 作溢出兜底。

### 2.5 24–28 路并发到底能撑多少人

"并发"不是"用户数"。一次问答只占用 10–15 秒，之后立刻释放；关键是**每小时能完成多少次问答**，而不是同时挂多少路。

单卡 48GB + 32B AWQ（24 路并发、单次 13 秒）：

- 吞吐 ≈ 24 ÷ 13 ≈ **1.8 次/秒 ≈ 6,600 次/小时**
- 1000 人 × 每人 5 次/天 = 5,000 次/天
- 即使全部挤在 3 小时内（1,700 次/小时），利用率也只有 **26%**
- 极端情况 1000 人集中在 1 小时内问完（5,000 次/小时），利用率 76%，会排队但不会崩

所以结论是：**24–28 路并发足够 1000 人日常使用**，真正的压力只在开学、评奖公示这类"半小时内全校涌入"的尖峰。尖峰靠三件事解决：语义缓存、短回答、在线 API 溢出，而不是把并发数堆到几百。

### 2.6 还想再提升吞吐的优化方向

按投入产出排序：

| 方向 | 做法 | 预期效果 | 代价 |
| --- | --- | --- | --- |
| 语义缓存预热 | 用检索日志统计高频问题，离线预生成答案写入缓存 | 命中直接 1 秒返回，等效容量 ×2 以上 | 无额外硬件 |
| 缩短回答 | `max_tokens` 2048→800，提示词要求"结论 + 条款号 + 分点" | 生成时间减半，吞吐 ×2 | 答案更短，需调提示词 |
| 前缀缓存 | vLLM 加 `--enable-prefix-caching` | 相同系统提示与知识前缀复用，prefill 降 30%–60% | 无 |
| 双档模型路由 | 简单问题走 7B/14B，复杂问题才用 32B | 吞吐 ×2–3 | 需在服务层加路由判断 |
| 换 14B FP8 | 用 Qwen2.5-14B-Instruct-FP8 替代 32B AWQ | 权重读取减半，解码快 1.5–2 倍，并发更高 | 复杂推理质量下降 |
| 加卡扩容 | 2×48GB 双副本 + Nginx 负载均衡 | 容量线性翻倍 | 硬件成本 |
| 深度问答限流 | 默认快速问答，深度问答按角色/次数限制 | 平均成本降 2–3 倍 | 复杂问题体验下降 |

其中**性价比最高的是前两项**：缓存和短回答都不花钱，但能把有效容量翻倍。校内问答的真实分布是"大量重复的流程问题 + 少量复杂的条款解读"，把重复问题挡在 LLM 之前，比换更大的卡有效得多。

如果确实要支撑 200 人同时在线（比如迎新现场集中咨询），再加一张卡做双副本即可，不需要重新设计架构。

---

## 三、改造点 2：新增 vLLM 作为上线方案

### 3.1 三种来源的分工

模型来源从两个扩展为三个，各自定位明确，互不替代：

| 来源 | 定位 | 典型场景 |
| --- | --- | --- |
| 在线 API | 原有调用方式，保持不变 | 没有 GPU 时使用，按量计费 |
| 本地 Ollama | 开发调试与本地演示 | 本机开发、离线 demo、功能验证 |
| vLLM 服务 | **正式上线方案** | 校内 GPU 服务器，追求低延迟与高吞吐 |

正式上线采用 vLLM。Ollama 的调度是小批量、请求基本排队，十几个人同时提问就会明显等待；vLLM 采用 continuous batching 与 PagedAttention，吞吐通常是 Ollama 的 3–10 倍，这是支撑校内多人访问的前提。

Ollama 保留但不再承担正式流量。它零依赖、一条命令即可运行，适合开发期在本机验证功能；正式环境的可用性由「在线 API」兜底——GPU 服务器维护或故障时临时切换过去，而不是退回 Ollama。

| 维度 | Ollama | vLLM |
| --- | --- | --- |
| 并发调度 | 小批量、请求基本排队 | continuous batching，请求动态拼批 |
| 显存管理 | 粗粒度 | PagedAttention，碎片少、可跑更长上下文 |
| 吞吐 | 低 | 通常是 Ollama 的 3–10 倍 |
| 协议 | 自有 API + OpenAI 兼容 | 原生 OpenAI 兼容 |
| 部署门槛 | 一条命令，Windows 可用 | 需 Linux/WSL2、CUDA 环境 |

### 3.2 部署命令（Linux）

```bash
# 1. 环境（CUDA 12.1+，Python 3.10/3.11）
python -m venv /opt/vllm && source /opt/vllm/bin/activate
pip install vllm

# 2. 启动服务
vllm serve Qwen/Qwen2.5-14B-Instruct-AWQ \
  --served-model-name qwen2.5-14b \
  --host 0.0.0.0 --port 8001 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.85 \
  --max-num-seqs 32
```

首次启动会从 HuggingFace 下载模型；校内网络建议先设置镜像：

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

### 3.3 系统里怎么接

管理端「模型配置」已新增「**vLLM 服务**」来源，直接选它即可：

| 配置项 | 填写值 |
| --- | --- |
| 来源 | vLLM 服务 |
| 服务地址 | `http://GPU服务器IP:8001/v1` |
| 模型名称 | `qwen2.5-14b`（与 `--served-model-name` 一致） |
| 温度 | 0.1 |
| 最大输出 tokens | 2048 |

注意四点：

1. 「vLLM 服务」来源**不校验在线 API 的密钥**，连接测试先请求 `/v1/models` 确认服务提供该模型，再发一次真实对话，结果准确；若 vLLM 用 `--api-key` 开了鉴权，在 `.env` 的 `VLLM_API_KEY` 填写。
2. 「在线 API」「vLLM 服务」「本地模型」三者互不影响，切换只改一条系统设置，下一次提问立即生效；
3. 默认值来自 `.env` 的 `VLLM_BASE_URL` 与 `VLLM_MODEL`，管理端保存后以数据库为准；
4. **vLLM 与 Ollama 共用同一张显卡时只能启用其中一个**，两者都会预占显存，同时跑会显存不足。

改动集中在 `backend/src/generation/llm.py`（构建模型）、`src/services/setting_service.py`（来源与生效地址）、`src/api/routes/admin_settings.py`（连接测试）以及管理端「模型配置」页面，业务链路不变。

### 3.4 开机自启（systemd）

```ini
# /etc/systemd/system/vllm.service
[Unit]
Description=vLLM OpenAI API
After=network.target

[Service]
User=vllm
WorkingDirectory=/opt/vllm
Environment=HF_ENDPOINT=https://hf-mirror.com
ExecStart=/opt/vllm/bin/vllm serve Qwen/Qwen2.5-14B-Instruct-AWQ \
  --served-model-name qwen2.5-14b --host 0.0.0.0 --port 8001 \
  --max-model-len 8192 --gpu-memory-utilization 0.75 --max-num-seqs 24
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now vllm
```

### 3.5 如果服务器只能是 Windows

vLLM 官方只支持 Linux。两条路：

- 装 WSL2 + Ubuntu 跑 vLLM（推荐，性能损失很小）；
- 继续用 Ollama，但必须设 `OLLAMA_NUM_PARALLEL=4`、`OLLAMA_MAX_LOADED_MODELS=1`，并接受并发能力弱于 vLLM。

---

## 四、改造点 3：embedding 与 rerank 上 GPU

### 4.1 改什么

`.env` 只改一行：

```ini
EMBEDDING_DEVICE=cuda
```

这一项同时控制两个模型（见 `backend/src/retrieval/vector_store.py` 与 `retrievers.py`）：

- `BAAI/bge-small-zh-v1.5`：查询向量化；
- `BAAI/bge-reranker-v2-m3`：对召回候选精排，是当前 21 秒里的大头。

### 4.2 依赖前提

CPU 版 torch 不支持 `cuda`，需要重装：

```bash
pip install --force-reinstall torch --index-url https://download.pytorch.org/whl/cu121
python -c "import torch; print(torch.cuda.is_available())"   # 必须 True
```

### 4.3 预期效果与显存

| 环节 | CPU | GPU（估） |
| --- | --- | --- |
| 查询向量化 | 0.3–1 s | < 0.1 s |
| 12 条候选精排 | 12.4 s | 0.5–1.5 s |
| 检索合计 | 12.4 s | 1–2.5 s |

显存占用：bge-small 约 0.5 GB，bge-reranker-v2-m3（fp16）约 2.5 GB。若与 vLLM 同卡，务必把 `--gpu-memory-utilization` 留出 4 GB 余量。

### 4.4 两个容易踩的坑

- **首次调用慢**：模型首次加载要几秒到十几秒，建议后端启动时预热（`preload_models()` 已有，确认启动日志里 embedding/rerank 已加载）。
- **reranker 版本**：`bge-reranker-v2-m3` 比 `bge-reranker-base` 慢约 2–3 倍但更准。上 GPU 后可以保留 v2-m3；如果检索量特别大，可做 A/B 对比后再决定。

---

## 五、改造点 4：检索参数调优

### 5.1 建议值

| 配置项 | 当前值 | 建议值 | 影响 |
| --- | --- | --- | --- |
| `VECTOR_TOP_K` | 50 | 50 | 向量召回条数，实测 50→20 只省 0.2 秒，不必为此缩小 |
| `BM25_TOP_K` | 50 | 50 | 同上 |
| `RERANK_INPUT_K` | 20 | 12 | 精排窗口，实测检索 21.1 → 12.4 秒，是唯一有效的一项 |
| `RERANK_TOP_N` | 5 | 5 | 最终给模型的证据条数，不建议动 |
| `CONTEXT_MAX_CHARS` | 6000 | 3500 | 拼给模型的上下文上限，影响首字与 API 计费 |

### 5.2 为什么这么调

- 精排耗时与候选数近似线性，CPU 上约 1 秒/条：20 → 12 条，检索从 21.1 秒降到 12.4 秒（实测）。
- 召回条数不是瓶颈：向量与 BM25 各召回 50 与 20，检索总耗时只差 0.2 秒，所以保持 50，不必为提速牺牲召回。
- 上下文从 6000 字降到 3500 字，单次输入 tokens 约减少 40%，在线 API 直接省 40% 费用；本地部署则降低首字延迟。
- `RERANK_TOP_N` 保持 5：再少会影响条款型问题的引用完整性（很多问题需要跨条比对）。
- 管理端存在检索策略预设时，`VECTOR_TOP_K`、`BM25_TOP_K` 以预设值为准，改 `.env` 不生效；`RERANK_INPUT_K` 不受预设影响。

### 5.3 调完必须回归

参数减小有掉召回的风险，改完跑一组真实问题对比：

1. 准备 30–50 条校内真实问题（可从管理端「检索日志」和「知识盲区」里导出）；
2. 记录改造前的命中情况：引用条数、是否答到条款、首字/总时长；
3. 改参数后重跑同一组问题，对比「答对率」与「平均耗时」；
4. 若答对率下降超过 5%，把 `RERANK_INPUT_K` 回调到 16。

仓库内可用 `backend/scripts/smoke_test.py` 与 `docs/evaluation` 下的评估流程做基础校验。

---

## 六、四步落地顺序

| 阶段 | 动作 | 验证标准 |
| --- | --- | --- |
| 0. 基线 | 记录当前首字、总时长、检索耗时、命中条数 | 有可对比的数字 |
| 1. 起 vLLM | 部署服务 → 管理端切「vLLM 服务」→ 测试连接 | 测试返回「连接正常」，问答可出字 |
| 2. 检索上 GPU | 重装 CUDA torch → `EMBEDDING_DEVICE=cuda` → 重启后端 | 检索耗时降到 3 s 内 |
| 3. 调参回归 | 按上表改精排窗口与上下文 → 跑 30 条问题 | 答对率不降、总时长再降 20%+ |

预期结果（单用户，14B / 单卡 48GB）：

- 首字：47.7 s → **0.5–2 s**
- 整段：60.8 s → **10–15 s**
- 检索：21.3 s → **1–3 s**

如果换 32B，生成环节再慢 30%–50%，整段约 15–20 s；如果改用在线 API，整段约 5–8 s。

---

## 七、风险与应对

| 风险 | 表现 | 应对 |
| --- | --- | --- |
| 显存不足 | vLLM 启动报 OOM | 降 `--max-num-seqs`、降 `--max-model-len` 到 4096、或改用更小量化 |
| 模型下载慢/失败 | 启动卡在拉模型 | 设 `HF_ENDPOINT=https://hf-mirror.com`，或提前离线拷贝模型目录 |
| torch 与 CUDA 不匹配 | `torch.cuda.is_available()` 为 False | 按显卡驱动版本重装对应 cu 版本 |
| Chroma 索引跨进程损坏 | 后端运行时其他进程打开索引报错 | 只允许后端进程访问 `indexes/chroma`，禁止外部脚本直连 |
| 单点故障 | GPU 服务器宕机则无法问答 | 保留在线 API 配置作为兜底，故障时切换 |
| 端口暴露 | 8001 直接暴露公网 | 只在内网开放，前置 Nginx 鉴权与限流 |

---

## 八、配置改动清单

系统已内置「vLLM 服务」来源，部署时只需动 `.env` 与一处管理端设置，业务代码无需修改。

```ini
# 生成侧：vLLM（OpenAI 兼容），管理端来源选「vLLM 服务」
VLLM_BASE_URL=http://GPU服务器IP:8001/v1
VLLM_MODEL=qwen2.5-14b
LLM_TEMPERATURE=0.1
LLM_MAX_TOKENS=2048
LLM_TIMEOUT_SECONDS=120

# 检索侧：索引与查询都上 GPU
EMBEDDING_DEVICE=cuda
EMBEDDING_BATCH_SIZE=64

# 精排窗口与上下文
RERANK_INPUT_K=12
RERANK_TOP_N=5
CONTEXT_MAX_CHARS=3500
```

管理端「模型配置」同步填写：来源 = vLLM 服务，服务地址与模型名同上。Ollama 的配置保持不动，需要时切回「本地模型」即可。

改完重启后端，`/health` 应返回 `status: ok` 且各库 `db/chroma/bm25` 三方一致。
