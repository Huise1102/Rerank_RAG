# 阶段二评估与回归说明

> 对应需求：`docs/阶段二.md` 第八章
>
> 更新日期：2026-11-13
>
> 当前状态：评估工具与样例已交付；真实 300 至 500 条黄金测试集尚未提供，正式基线与阶段二回归数据待补。

---

## 一、当前进展

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| 评估模块 | 已完成 | 位于 `backend/src/evaluation/` |
| 测试集校验 | 已完成 | 校验必填字段、重复编号、问题长度与敏感信息 |
| 检索评估 | 已完成 | Recall@k、Precision@k、MRR、nDCG、Hit、关键词覆盖率 |
| 生成评估 | 已完成 | 忠实度、答案相关性、引用覆盖率 |
| 管理端报告页 | 已完成 | `/admin/evaluation`，管理员可运行，管理员与部门管理员可查看 |
| 脱敏样例 | 已完成 | `backend/tests/fixtures/evaluation_sample.jsonl`，12 条 |
| 真实黄金测试集 | 未完成 | 需教务处提供真实工单并完成脱敏、标注与审核 |
| 正式基线 | 未完成 | 依赖真实测试集 |
| 权限与时效硬门槛的评估数据 | 部分完成 | 由自动化测试覆盖，尚未纳入真实测试集统计 |

---

## 二、测试集格式

使用 JSONL，每行一条 JSON。字段以当前实现为准。

必填字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 样本编号，测试集内唯一 |
| `question` | string | 用户问题，长度不少于 4 个字符 |
| `category` | string | 类别，用于分布统计 |
| `relevant_doc_ids` | string 数组 | 相关文档 ID，非空 |

可选字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `relevant_chunk_ids` | string 数组 | 相关块 ID；填写后按块级判定，否则按文档级判定 |
| `expected_keywords` | string 数组 | 期望关键词，用于计算关键词覆盖率 |
| `reference_answer` | string | 参考答案要点 |
| `difficulty` | string | easy、medium、hard |

示例：

```json
{"id":"campus-001","question":"新生入学后多久需要进行学籍注册？","category":"学籍管理","relevant_doc_ids":["3"],"expected_keywords":["学籍","注册"],"reference_answer":"","difficulty":"easy"}
```

校验规则：

1. 编号缺失或重复记为错误。
2. 必填字段缺失记为错误。
3. 问题过短记为错误。
4. `relevant_doc_ids` 为空或非数组记为错误。
5. 问题或参考答案中出现身份证号、手机号、邮箱时给出脱敏警告。
6. 样本少于 300 条时给出数量不足警告。

真实测试集建议放在 `backend/evaluation_data/`，该目录已加入 `.gitignore`；仓库只保留脱敏样例与格式说明。

---

## 三、类别与规模要求

真实测试集规模为 300 至 500 条，首期建议 400 条。建议类别分布：

| 类别 | 建议占比 |
| --- | --- |
| 语义泛化 | 40% |
| 术语精确 | 25% |
| 多跳推理 | 20% |
| 时效与版本 | 10% |
| 权限与拒答 | 5% |

每条样本需要标注所属知识库、部门、相关文档或块、参考答案要点与期望行为，涉及个人信息的工单不得进入测试集。

---

## 四、执行方式

### 4.1 命令行

在 `backend` 目录执行：

```powershell
.\.venv\Scripts\python.exe -m src.evaluation.cli validate --testset evaluation_data\golden_v1.jsonl
.\.venv\Scripts\python.exe -m src.evaluation.cli retrieval --testset evaluation_data\golden_v1.jsonl --kb-id 3 --top-k 10 --output ..\docs\evaluation\baseline_v1.md
.\.venv\Scripts\python.exe -m src.evaluation.cli generation --testset evaluation_data\golden_v1.jsonl --kb-id 3 --limit 100 --output ..\docs\evaluation\generation_v1.md
```

参数说明：

| 参数 | 说明 |
| --- | --- |
| `--testset` | JSONL 路径，必填 |
| `--kb-id` | 检索与生成评估必填 |
| `--strategy-id` | 指定检索策略，缺省使用系统默认 |
| `--limit` | 限制样本数，便于小批量试跑 |
| `--top-k` | 检索评估截断位置，默认 10 |
| `--role` | 评估使用的角色，可选 admin、dept_admin、staff、student，默认 admin |
| `--department-id` | 评估使用的部门 |
| `--output` | 报告输出路径，后缀为 `.md` 或 `.json` |

### 4.2 管理端

进入「评估回归」页面，选择测试集路径、知识库、评估类型与角色，点击运行；结果写入 `evaluation_reports` 表，可在报告列表查看 Markdown 内容与逐题明细。

### 4.3 权限类样本

权限与拒答样本必须使用对应的 `student`、`staff`、`dept_admin` 账号或 `--role` 参数执行，不能只用管理员跑全量后推断。管理员默认可见范围最大，用管理员跑权限样本会掩盖越权问题。

---

## 五、基线与回归流程

1. 在改造前使用同一测试集、同一分块策略、同一模型配置跑一次基线，记录检索、生成与性能指标。
2. 阶段二完成后复跑，比较 Recall@k、MRR、nDCG、忠实度、答案相关性、引用覆盖率与 P95。
3. 每次发布新版本制度、修改分块策略、修改检索参数或修改权限规则，都必须重跑回归。
4. 指标下降超过门槛时先定位原因，不得直接上线；降级项需要确认并记录。

硬门槛：

- 越权召回率为 0。
- 废止文档召回次数为 0。
- 旧版本引用次数为 0。
- 引用中的文档均属于当前用户可访问范围。

软门槛建议值：

| 指标 | 建议值 |
| --- | --- |
| Recall@5 | 不低于基线 2 个百分点 |
| 引用正确率 | 不低于 90% |
| 忠实度平均分 | 不低于 4.0（5 分制） |
| 越界引用率 | 不高于 1% |
| 拒答正确率 | 不低于 90% |

说明：当前检索与生成评估统计 Recall、MRR、nDCG、忠实度、答案相关性与引用覆盖率；越权与废止召回、旧版本引用等硬门槛由 `backend/tests/test_acl_scope.py` 与 `backend/tests/test_lifecycle.py` 自动化覆盖，真实测试集接入后应补充对应统计列。

---

## 六、本次样例集结果

执行命令：

```powershell
python -m src.evaluation.cli validate --testset tests/fixtures/evaluation_sample.jsonl
```

结果：

| 项目 | 值 |
| --- | --- |
| 样本数 | 12 |
| 校验结果 | 通过，无错误 |
| 警告 | 低于 300 条下限 |
| 类别分布 | 学籍管理 5，考试管理 2，日常管理 1，毕业管理 1，学位管理 1，奖助管理 1，学生权益 1 |

样例集只用于验证格式与工具链路，不作为质量结论，也未生成正式基线。

---

## 七、回归记录模板

真实测试集到位后按以下表格填写，建议每次回归新增一节并注明日期、知识库版本与模型配置。

| 项目 | 基线 | 阶段二 | 差异 | 是否达标 |
| --- | --- | --- | --- | --- |
| 样本数 |  |  |  |  |
| Recall@5 |  |  |  |  |
| Recall@10 |  |  |  |  |
| Precision@5 |  |  |  |  |
| MRR |  |  |  |  |
| nDCG@10 |  |  |  |  |
| 忠实度 |  |  |  |  |
| 答案相关性 |  |  |  |  |
| 引用覆盖率 |  |  |  |  |
| 越权召回率 |  |  |  | 0 |
| 废止文档召回次数 |  |  |  | 0 |
| 旧版本引用次数 |  |  |  | 0 |
| 拒答正确率 |  |  |  |  |
| P50 延迟 |  |  |  |  |
| P95 延迟 |  |  |  |  |

配置记录项：知识库 ID、分块策略、嵌入模型、精排模型、大模型、检索策略 ID、执行角色与部门。
