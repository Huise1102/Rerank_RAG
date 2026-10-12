"""阶段 5 —— 评估模块自测（生成与裁判都用 mock，不消耗 LLM token）。

覆盖 metrics 数学正确性、testset 加载与校验、retrieval_eval 指标与导出、
judge 的 JSON 容错与并发打分、generation_eval 聚合与导出、visualize 出图、
ablation 表结构。检索评估真实调用检索器，默认只用较快的 bm25/vector。

运行：.venv\\Scripts\\python.exe scripts\\test_evaluation.py
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import settings  # noqa: E402
from src.evaluation import ablation as ab  # noqa: E402
from src.evaluation import metrics as M  # noqa: E402
from src.evaluation import retrieval_eval as re  # noqa: E402
from src.evaluation import testset as ts  # noqa: E402
from src.evaluation import visualize as viz  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [OK]   {name}  {detail}")
    else:
        FAILED.append((name, detail))
        print(f"  [FAIL] {name}  {detail}")



def test_metrics() -> None:
    print("\n[1/7] metrics 数学正确性（与手算值核对）")
    rel = ["A", "B"]
    r = ["X", "A", "Y", "B", "Z"]  # A 在第2位，B 在第4位

    check("Recall@1 = 0", M.recall_at_k(r, rel, 1) == 0.0, str(M.recall_at_k(r, rel, 1)))
    check("Recall@2 = 0.5", M.recall_at_k(r, rel, 2) == 0.5, str(M.recall_at_k(r, rel, 2)))
    check("Recall@4 = 1.0", M.recall_at_k(r, rel, 4) == 1.0, str(M.recall_at_k(r, rel, 4)))
    check("Recall@5 = 1.0（不超 1）", M.recall_at_k(r, rel, 5) == 1.0)

    check("MRR = 0.5（首个命中第2位）", abs(M.reciprocal_rank(r, rel, 10) - 0.5) < 1e-9,
          str(M.reciprocal_rank(r, rel, 10)))
    check("MRR@1 = 0（截断到第1位无命中）", M.reciprocal_rank(r, rel, 1) == 0.0)
    check("Precision@4 = 0.5", M.precision_at_k(r, rel, 4) == 0.5)
    check("Hit@2 = 1", M.hit_at_k(r, rel, 2) == 1.0)
    check("Hit@1 = 0", M.hit_at_k(r, rel, 1) == 0.0)

    # nDCG 手算：DCG=0/log2(2)+1/log2(3)+0+1/log2(5)=0.6309+0.4307=1.0616
    #            IDCG=1/log2(2)+1/log2(3)=1.6309  ->  0.6509
    expected = (1 / math.log2(3) + 1 / math.log2(5)) / (1 + 1 / math.log2(3))
    got = M.ndcg_at_k(r, rel, 4)
    check("nDCG@4 与手算一致", abs(got - expected) < 1e-9,
          f"{got:.4f}（手算 {expected:.4f}）")
    check("nDCG 完美排序 = 1.0", abs(M.ndcg_at_k(["A", "B", "X"], rel, 2) - 1.0) < 1e-9)
    check("nDCG 全不命中 = 0", M.ndcg_at_k(["X", "Y"], rel, 2) == 0.0)

    # 边界：relevant 为空
    check("relevant 空时 Recall=0（不除零）", M.recall_at_k(r, [], 5) == 0.0)
    check("relevant 空时 nDCG=0（不除零）", M.ndcg_at_k(r, [], 5) == 0.0)
    check("retrieved 空时 Recall=0", M.recall_at_k([], rel, 5) == 0.0)
    check("k=0 时不报错", M.recall_at_k(r, rel, 0) == 0.0)

    # 单条样本完整指标
    item = M.evaluate_item(r, rel, question="q", category="语义泛化",
                           recall_ks=(1, 2, 4), mrr_k=10, ndcg_k=4)
    check("evaluate_item 首个命中名次 = 2", item.first_hit_rank == 2, str(item.first_hit_rank))
    check("evaluate_item 记录命中与漏检",
          item.hits == ["A", "B"] and item.missed == [], f"hits={item.hits} missed={item.missed}")
    row = item.to_row("bm25")
    check("to_row 含 recall@k 列", "recall@1" in row and "recall@4" in row, str(list(row)[:8]))

    agg = M.aggregate([item, item], (1, 2, 4))
    check("aggregate 求平均正确", agg["recall@2"] == 0.5 and agg["mrr"] == 0.5, str(agg))



def _real_chunk_ids(n: int = 2, strategy: str = "recursive") -> list[str]:
    """从真实索引取若干 chunk_id，用于构造校验用的测试集。"""
    from src.ingest import splitter

    chunks = splitter.load_chunks(strategy).chunks
    return [c.chunk_id for c in chunks[:n]]


def test_testset() -> None:
    print("\n[2/7] testset 加载与校验")

    ids = _real_chunk_ids(2)
    check("能取到真实 chunk_id", len(ids) == 2, f"{ids[:1]}…")

    tmp = Path(tempfile.mkdtemp(prefix="ts_test_"))

    # 正常用例
    good = tmp / "good.json"
    good.write_text(json.dumps([
        {"question": "问题一", "relevant_chunk_ids": ids[:1], "category": "术语精确"},
        {"question": "问题二", "relevant_chunk_ids": ids[1:], "category": "semantic"},
        {"question": "问题三", "relevant_chunk_ids": ids, "category": "多跳推理"},
    ], ensure_ascii=False), encoding="utf-8")
    data = ts.load_testset(good)
    check("正常测试集加载成功", data.is_valid and len(data) == 3, data.summary())
    check("英文 category 别名被归一化",
          data.items[1].category == "语义泛化", data.items[1].category)
    check("类别分布统计正确",
          data.category_counts() == {"术语精确": 1, "语义泛化": 1, "多跳推理": 1},
          str(data.category_counts()))

    # 校验：chunk_id 不存在
    bad_ids = tmp / "badids.json"
    bad_ids.write_text(json.dumps([
        {"question": "问题", "relevant_chunk_ids": ["recursive:不存在.pdf:p1:c0:deadbeef"],
         "category": "术语精确"},
    ], ensure_ascii=False), encoding="utf-8")
    bad_data = ts.load_testset(bad_ids)
    check("不存在的 chunk_id 被识别为错误",
          bool(bad_data.errors) and not bad_data.is_valid,
          bad_data.errors[0].message[:60] if bad_data.errors else "")

    # 校验：空字段 / 非法类别 / 重复问题
    messy = tmp / "messy.json"
    messy.write_text(json.dumps([
        {"question": "", "relevant_chunk_ids": ids[:1], "category": "术语精确"},
        {"question": "重复题", "relevant_chunk_ids": [], "category": "乱写"},
        {"question": "重复题", "relevant_chunk_ids": ids[:1], "category": "语义泛化"},
    ], ensure_ascii=False), encoding="utf-8")
    messy_data = ts.load_testset(messy, check_chunk_ids=False)
    msgs = " ".join(x.message for x in messy_data.issues)
    check("空 question 被报错", "question 为空" in msgs)
    check("空 relevant_chunk_ids 被报错", "relevant_chunk_ids 为空" in msgs)
    check("非法 category 给出警告", "category 缺失或无法识别" in msgs)
    check("重复问题被识别", "重复" in msgs)

    # 文件不存在时的提示
    try:
        ts.load_testset(tmp / "nope.json")
        check("文件不存在时抛异常", False, "未抛异常")
    except FileNotFoundError as exc:
        check("文件不存在时给出标注指引", "标注" in str(exc) and "candidates" in str(exc),
              str(exc).splitlines()[0][:50])

    # 兼容 {"items": [...]} 结构
    wrapped = tmp / "wrapped.json"
    wrapped.write_text(json.dumps({"items": [
        {"question": "q", "relevant_chunk_ids": ids[:1], "category": "术语精确"}]},
        ensure_ascii=False), encoding="utf-8")
    check("兼容 items 包裹结构", len(ts.load_testset(wrapped)) == 1)

    # 保存回写
    out = tmp / "saved.json"
    ts.save_testset(data, out)
    check("save_testset 回读一致", len(ts.load_testset(out)) == 3)

    # 标注候选生成
    cands = ts.make_annotation_candidates(per_file=1, max_chars=60)
    check("标注候选可生成", len(cands) > 0, f"{len(cands)} 条")
    check("候选含 chunk_id 与摘要",
          bool(cands[0]["relevant_chunk_ids"]) and bool(cands[0]["_preview"]),
          f"{cands[0]['_source']} p{cands[0]['_page']}")

    # 按页码解析
    resolved = ts.resolve_by_page([(cands[0]["_source"], cands[0]["_page"])])
    check("resolve_by_page 能反查 chunk_id", len(resolved) > 0, f"{len(resolved)} 个")

    shutil.rmtree(tmp, ignore_errors=True)



class MockRetrievalResponse:
    """伪检索响应，用于在不跑真实精排的情况下验证评估流程。"""

    def __init__(self, ids: list[str], seconds: float = 0.05) -> None:
        self.chunk_ids = ids
        self.timing = {"recall": seconds, "total": seconds}


def _build_eval_testset(ids: list[str]) -> ts.TestSet:
    items = [
        ts.TestItem(question="问题A", relevant_chunk_ids=[ids[0]], category="术语精确", index=1),
        ts.TestItem(question="问题B", relevant_chunk_ids=[ids[1]], category="语义泛化", index=2),
        ts.TestItem(question="问题C", relevant_chunk_ids=ids[:2], category="多跳推理", index=3),
    ]
    return ts.TestSet(items=items, path=Path("memory"))


def test_retrieval_eval() -> None:
    print("\n[3/7] retrieval_eval（伪检索，验证指标与导出）")
    ids = _real_chunk_ids(3)
    testset = _build_eval_testset(ids)

    # 期望值须先算清；这里最容易把 Recall@1 当成 Hit@1：相关文档多于一个时，
    # 命中一个只算部分召回 0.5，而不是 1.0。
    #   题A：relevant=[ids0]，ids0 在第 2 位      -> Recall@1=0, MRR=0.5
    #   题B：relevant=[ids1]，ids1 在第 1 位      -> Recall@1=1, MRR=1.0
    #   题C：relevant=[ids0,ids1]，前 3 位都给全  -> Recall@1=0.5, Recall@5=1.0
    plans = {
        "问题A": [ids[1], ids[0], ids[2]],
        "问题B": [ids[1], ids[0], ids[2]],
        "问题C": [ids[0], ids[1], ids[2]],
    }
    # 期望：MRR=(0.5+1.0+1.0)/3；Recall@1=(0+1+0.5)/3；Recall@5=(1+1+1)/3
    exp_mrr = (0.5 + 1.0 + 1.0) / 3
    exp_r1 = (0.0 + 1.0 + 0.5) / 3
    exp_r5 = (1.0 + 1.0 + 1.0) / 3

    class _FakePipeline:
        def retrieve(self, question, strategy=None, top_k=None, write_log=False):
            return MockRetrievalResponse(plans[question])

    class _FakeModule:
        @staticmethod
        def get_pipeline(chunk_strategy=None):
            return _FakePipeline()

    import src.retrieval.pipeline as real_pl
    original = real_pl.get_pipeline
    real_pl.get_pipeline = _FakeModule.get_pipeline  # type: ignore[assignment]
    tmp_out = Path(tempfile.mkdtemp(prefix="eval_out_"))
    try:
        result = re.evaluate_all(testset, strategies=["bm25", "vector"],
                                 show_progress=False)
        check("评估返回两个策略结果", set(result.results) == {"bm25", "vector"},
              str(list(result.results)))

        bm25 = result.results["bm25"]
        check("逐题指标条数正确", len(bm25.items) == 3, f"{len(bm25.items)} 条")
        check("MRR 平均值正确", abs(bm25.overall([5])["mrr"] - exp_mrr) < 1e-3,
              f"{bm25.overall([5])['mrr']}（期望 {exp_mrr:.4f}）")
        check("Recall@5 平均值正确", abs(bm25.overall([5])["recall@5"] - exp_r5) < 1e-3,
              f"{bm25.overall([5])['recall@5']}（期望 {exp_r5:.4f}）")
        check("Recall@1 平均值正确", abs(bm25.overall([1])["recall@1"] - exp_r1) < 1e-3,
              f"{bm25.overall([1])['recall@1']}（期望 {exp_r1:.4f}）")
        # 题A 的正确答案在第 2 位，nDCG@1 应为 0，nDCG@3 应大于 0
        item_a = bm25.items[0]
        check("逐题 first_hit_rank 正确", item_a.first_hit_rank == 2, str(item_a.first_hit_rank))
        check("题A nDCG@1=0 且 nDCG@5>0（位置折损生效）",
              M.ndcg_at_k(plans["问题A"], [ids[0]], 1) == 0.0
              and M.ndcg_at_k(plans["问题A"], [ids[0]], 5) > 0.0,
              f"nDCG@1={M.ndcg_at_k(plans['问题A'],[ids[0]],1)} "
              f"nDCG@5={M.ndcg_at_k(plans['问题A'],[ids[0]],5):.4f}")

        df = result.summary_frame()
        check("summary_frame 列齐全",
              {"strategy", "recall@5", "recall@10", "mrr", "ndcg"} <= set(df.columns),
              str(list(df.columns)))

        cat = result.category_frame()
        check("category_frame 按类别分组", len(cat) == 6, f"{len(cat)} 行（2策略×3类）")

        per_q = result.per_question_frame()
        check("per_question_frame 逐题明细", len(per_q) == 6, f"{len(per_q)} 行")

        md = result.markdown_table()
        # Markdown 表格的特征是每行以 | 开头；缺 tabulate 时会退化为纯文本对齐表格
        check("markdown_table 可用", bool(md.strip()) and "strategy" in md,
              md.strip().splitlines()[0][:50])

        written = re.save_retrieval_results(result, tmp_out)
        check("导出 retrieval_comparison.csv", "main" in written and written["main"].exists())
        check("导出 by_category.csv", "by_category" in written and written["by_category"].exists())
        check("导出 per_question.csv", "per_question" in written and written["per_question"].exists())
        check("导出 markdown", "markdown" in written and written["markdown"].exists())

        # CSV 可被 pandas 读回（utf-8-sig）
        import pandas as pd

        back = pd.read_csv(written["main"], encoding="utf-8-sig")
        check("CSV 可读回且列完整", "recall@5" in back.columns and len(back) == 2,
              f"{len(back)} 行")
    finally:
        real_pl.get_pipeline = original  # type: ignore[assignment]
        shutil.rmtree(tmp_out, ignore_errors=True)



def _mock_judge_client(payload: str | callable):
    """
    构造裁判用的 mock 客户端。

    LLMJudge.score 调的是 self.client.client.chat.completions.create：外层是
    LLMClient、内层才是 OpenAI 客户端，因此 mock 必须同时提供 .client 与 .chat。
    """

    class _Completions:
        def create(self, **params):
            content = payload(params) if callable(payload) else payload
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                usage=SimpleNamespace(total_tokens=120),
            )

    inner = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))

    class _OuterClient:
        def __init__(self) -> None:
            self.client = inner      # 供 LLMJudge 取用
            self.chat = inner.chat   # 同时兼容直接调 .chat 的写法

    return _OuterClient()


def test_judge() -> None:
    print("\n[4/7] judge（mock 裁判，零 token）")
    from src.evaluation.judge import LLMJudge, aggregate_scores

    contexts = [
        SimpleNamespace(text="应在规定期限内提出申请。", source="测试.pdf", page=18),
        {"text": "奖励的申请条件。", "source": "测试.pdf", "page": 53},
    ]

    # 标准 JSON 输出
    good = json.dumps({
        "faithfulness": {"score": 5, "reason": "完全有依据"},
        "answer_relevancy": {"score": 4, "reason": "基本切题"},
    }, ensure_ascii=False)
    judge = LLMJudge(client=_mock_judge_client(good))
    s = judge.score("适用范围？", contexts, "规定期限[1]。", strategy="bm25", category="术语精确")
    check("标准 JSON 解析成功", s.ok and s.scores.get("faithfulness") == 5.0, str(s.scores))
    check("理由被保留", s.reasons.get("faithfulness") == "完全有依据", s.reasons.get("faithfulness", ""))
    check("token 被记录", s.tokens == 120, str(s.tokens))

    # 带代码块围栏的输出（模型常见行为）
    fenced = "```json\n" + good + "\n```"
    s2 = LLMJudge(client=_mock_judge_client(fenced)).score("q", contexts, "a")
    check("带 ```json 围栏也能解析", s2.ok and s2.scores.get("faithfulness") == 5.0, str(s2.scores))

    # 前后有多余文字
    noisy = "好的，我的评分如下：\n" + good + "\n以上就是我的评价。"
    s3 = LLMJudge(client=_mock_judge_client(noisy)).score("q", contexts, "a")
    check("前后有多余文字也能解析", s3.ok, str(s3.scores))

    # 完全无法解析（正则也提不出）
    s4 = LLMJudge(client=_mock_judge_client("我拒绝评分。")).score("q", contexts, "a", retries=0)
    check("无法解析时记为失败而非瞎猜", not s4.ok and bool(s4.error), s4.error[:40])

    # 越界分数被丢弃
    out_of_range = json.dumps({
        "faithfulness": {"score": 99, "reason": "越界"},
        "answer_relevancy": {"score": 3, "reason": "正常"},
    }, ensure_ascii=False)
    s5 = LLMJudge(client=_mock_judge_client(out_of_range)).score("q", contexts, "a")
    check("越界分数被丢弃、合法维度保留",
          "faithfulness" not in s5.scores and s5.scores.get("answer_relevancy") == 3.0,
          str(s5.scores))

    # 空答案
    s6 = judge.score("q", contexts, "   ")
    check("空答案直接失败", not s6.ok and "为空" in s6.error, s6.error)

    # 并发批量打分
    cases = [{"question": f"q{i}", "contexts": contexts, "answer": "a", "strategy": "bm25",
              "category": "术语精确"} for i in range(6)]
    batch = LLMJudge(client=_mock_judge_client(good)).score_batch(cases, show_progress=False, workers=3)
    check("并发批量打分条数正确", len(batch) == 6, f"{len(batch)} 条")
    check("并发结果全部成功", all(b.ok for b in batch))

    # 聚合（分母只算成功条数）
    agg = aggregate_scores(batch)
    check("聚合平均分正确", agg["faithfulness"] == 5.0 and agg["n_scored"] == 6, str(agg))
    mixed = aggregate_scores([batch[0], s4])  # 一条失败
    check("失败条目不按 0 分计入均值",
          mixed["faithfulness"] == 5.0 and mixed["n_scored"] == 1 and mixed["errors"] == 1,
          str(mixed))

    # 非法维度
    try:
        LLMJudge(client=_mock_judge_client(good), metrics=["不存在的维度"])
        check("非法评分维度被拒绝", False, "未抛异常")
    except ValueError:
        check("非法评分维度被拒绝", True)

    # 上下文渲染
    rendered = LLMJudge.format_context(contexts)
    check("参考资料渲染含编号与来源",
          "[1] 来源：测试.pdf 页码：18" in rendered and "[2] 来源：" in rendered,
          rendered.splitlines()[0][:40])



def test_generation_eval() -> None:
    print("\n[5/7] generation_eval（mock 生成 + mock 裁判，零 token）")
    from src.evaluation import generation_eval as ge
    from src.evaluation.judge import LLMJudge

    ids = _real_chunk_ids(3)
    testset = _build_eval_testset(ids)

    # mock 生成客户端：返回固定答案 + usage
    reply = json.dumps({"faithfulness": {"score": 4, "reason": "ok"},
                        "answer_relevancy": {"score": 5, "reason": "ok"}}, ensure_ascii=False)

    class _GenCompletions:
        def create(self, **params):
            if params.get("stream"):
                def gen():
                    yield SimpleNamespace(choices=[SimpleNamespace(
                        delta=SimpleNamespace(content="答案"), finish_reason=None)])
                return gen()
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content="这是答案[1]。", reasoning_content=None),
                    finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=900, completion_tokens=30, total_tokens=930),
            )

    from src.generation.llm import LLMClient

    # 同样注意层级：LLMClient 内部用 self.client.chat.completions
    _gen_inner = SimpleNamespace(chat=SimpleNamespace(completions=_GenCompletions()))

    class _GenOuter:
        def __init__(self) -> None:
            self.client = _gen_inner
            self.chat = _gen_inner.chat

    gen_client = LLMClient(client=_GenOuter())

    judge = LLMJudge(client=_mock_judge_client(reply))

    class _FakePipeline:
        def retrieve(self, question, strategy=None, top_k=None, write_log=False):
            class R:
                chunk_ids = ids
                timing = {"recall": 0.01, "total": 0.01}
                results = [
                    SimpleNamespace(text="应在规定期限内提出申请。", source="测试.pdf", page=18)
                ]
                warnings: list[str] = []
            return R()

    import src.retrieval.pipeline as real_pl
    original = real_pl.get_pipeline
    real_pl.get_pipeline = lambda cs=None: _FakePipeline()  # type: ignore[assignment]
    tmp_out = Path(tempfile.mkdtemp(prefix="gen_out_"))
    try:
        result = ge.evaluate_generation(
            testset, strategies=["bm25"], judge=judge, llm_client=gen_client,
            show_progress=False,
        )
        check("生成评估产生结果", len(result.scores.get("bm25", [])) == 3,
              f"{len(result.scores.get('bm25', []))} 条")

        df = result.summary_frame()
        check("summary_frame 含评分列",
              {"faithfulness", "answer_relevancy", "n_scored"} <= set(df.columns),
              str(list(df.columns)))
        check("平均分正确", float(df["faithfulness"].iloc[0]) == 4.0
              and float(df["answer_relevancy"].iloc[0]) == 5.0,
              f"faith={df['faithfulness'].iloc[0]} relev={df['answer_relevancy'].iloc[0]}")
        check("生成 token 被统计", result.tokens.get("bm25", 0) == 930 * 3,
              str(result.tokens))

        cat = result.category_frame()
        check("分维度表生成", len(cat) == 3, f"{len(cat)} 行")

        per_q = result.per_question_frame()
        check("逐题明细含答案长度与引用数",
              "answer_chars" in per_q.columns and "citations" in per_q.columns,
              str(list(per_q.columns)[:8]))

        written = ge.save_generation_results(result, tmp_out)
        check("导出 generation_comparison.csv", "main" in written and written["main"].exists())
        check("导出 markdown 且含裁判说明",
              "markdown" in written and "LLM-as-Judge" in written["markdown"].read_text(encoding="utf-8"))
    finally:
        real_pl.get_pipeline = original  # type: ignore[assignment]
        shutil.rmtree(tmp_out, ignore_errors=True)



def test_visualize() -> None:
    print("\n[6/7] visualize 出图")
    import pandas as pd

    tmp_out = Path(tempfile.mkdtemp(prefix="fig_"))

    class _Stub:
        def __init__(self, main: "pd.DataFrame", cat: "pd.DataFrame | None" = None) -> None:
            self._main, self._cat = main, cat if cat is not None else pd.DataFrame()

        def summary_frame(self):
            return self._main

        def category_frame(self):
            return self._cat

    retrieval = _Stub(pd.DataFrame([
        {"strategy": "vector", "label": "向量", "n": 10, "recall@1": 0.3, "recall@5": 0.6,
         "recall@10": 0.7, "mrr": 0.42, "ndcg": 0.5, "avg_seconds": 2.1},
        {"strategy": "bm25", "label": "BM25", "n": 10, "recall@1": 0.5, "recall@5": 0.8,
         "recall@10": 0.8, "mrr": 0.6, "ndcg": 0.68, "avg_seconds": 0.02},
        {"strategy": "hybrid_rerank", "label": "混合+精排", "n": 10, "recall@1": 0.7,
         "recall@5": 0.9, "recall@10": 0.9, "mrr": 0.78, "ndcg": 0.83, "avg_seconds": 19.0},
    ]), pd.DataFrame([
        {"strategy": "bm25", "label": "BM25", "category": "术语精确", "n": 4,
         "recall@5": 0.9, "recall@10": 0.9, "mrr": 0.8, "ndcg": 0.85},
        {"strategy": "vector", "label": "向量", "category": "语义泛化", "n": 3,
         "recall@5": 0.7, "recall@10": 0.7, "mrr": 0.6, "ndcg": 0.65},
    ]))

    generation = _Stub(pd.DataFrame([
        {"strategy": "bm25", "label": "BM25", "n": 10, "n_scored": 10,
         "faithfulness": 4.2, "answer_relevancy": 4.4, "errors": 0, "avg_seconds": 4.1},
        {"strategy": "hybrid_rerank", "label": "混合+精排", "n": 10, "n_scored": 10,
         "faithfulness": 4.7, "answer_relevancy": 4.6, "errors": 0, "avg_seconds": 24.0},
    ]))

    chunk_abl = _Stub(pd.DataFrame([
        {"strategy": "fixed", "label": "固定长度", "n": 10, "recall@1": 0.4, "recall@5": 0.7,
         "recall@10": 0.75, "mrr": 0.52, "ndcg": 0.61, "avg_seconds": 2.0},
        {"strategy": "recursive", "label": "递归语义", "n": 10, "recall@1": 0.5,
         "recall@5": 0.8, "recall@10": 0.85, "mrr": 0.62, "ndcg": 0.7, "avg_seconds": 2.1},
    ]))

    rerank_abl = _Stub(pd.DataFrame([
        {"top_k": 3, "n": 10, "recall@1": 0.6, "recall@3": 0.8, "recall@5": 0.8,
         "mrr": 0.68, "ndcg": 0.74, "avg_seconds": 12.0},
        {"top_k": 5, "n": 10, "recall@1": 0.7, "recall@3": 0.85, "recall@5": 0.9,
         "mrr": 0.76, "ndcg": 0.82, "avg_seconds": 19.0},
        {"top_k": 8, "n": 10, "recall@1": 0.7, "recall@3": 0.85, "recall@5": 0.9,
         "mrr": 0.77, "ndcg": 0.83, "avg_seconds": 29.0},
    ]))

    try:
        written = viz.render_all(retrieval, generation, chunk_abl, rerank_abl, out_dir=tmp_out)
        check("共生成 5 张图", len(written) == 5, f"{len(written)} 张: {list(written)}")
        for name, path in written.items():
            size = path.stat().st_size if path.exists() else 0
            check(f"图 {name} 非空", size > 3000, f"{size} B")
        check("中文字体是否可用（影响标签语言）", isinstance(viz._has_cjk(), bool),
              f"可用={viz._has_cjk()}，标签示例={viz.L('召回@5','recall@5')}")
        # 缺数据时应跳过而非报错
        partial = viz.render_all(retrieval, None, None, None, out_dir=tmp_out)
        check("缺数据时只画有数据的图", len(partial) == 2, f"{len(partial)} 张")
    finally:
        shutil.rmtree(tmp_out, ignore_errors=True)



def test_ablation() -> None:
    print("\n[7/7] ablation 消融")
    from src.evaluation.metrics import evaluate_item
    from src.evaluation.retrieval_eval import RetrievalEvalResult, StrategyResult

    ids = _real_chunk_ids(3)

    # 可用分块策略探测
    ready = ab.available_chunk_strategies()
    check("能探测已建好的分块索引", isinstance(ready, list) and len(ready) > 0, f"可用: {ready}")

    def _make_result(strategy: str, hit_at_rank: int, seconds: float) -> StrategyResult:
        """构造一个真实的 StrategyResult（含 2 条样本），而不是拿 None 充数。"""
        result = StrategyResult(strategy=strategy, seconds=seconds * 2)
        for i in range(2):
            retrieved = ids[hit_at_rank - 1 :] + ids[: hit_at_rank - 1]
            result.items.append(
                evaluate_item(retrieved, [ids[0]], question=f"q{i}", category="语义泛化",
                              recall_ks=(1, 3, 5), mrr_k=10, ndcg_k=10, seconds=seconds)
            )
        return result

    # Top-k 消融：Top-3 命中较晚（指标低、耗时短），Top-8 命中早但耗时高
    topk_result = ab.RerankTopKResult(
        results={
            3: _make_result("hybrid_rerank", 3, 12.0),
            5: _make_result("hybrid_rerank", 1, 19.0),
            8: _make_result("hybrid_rerank", 1, 29.0),
        },
        chunk_strategy="recursive",
        testset_path="memory",
    )
    df = topk_result.summary_frame()
    check("Top-k 消融表结构正确",
          list(df["top_k"]) == [3, 5, 8] and "recall@1" in df.columns and "avg_seconds" in df.columns,
          str(list(df.columns)))
    check("Top-k 消融指标随档位变化",
          float(df.loc[df["top_k"] == 3, "recall@1"].iloc[0])
          < float(df.loc[df["top_k"] == 5, "recall@1"].iloc[0]),
          f"top3={df.loc[df['top_k']==3,'recall@1'].iloc[0]} "
          f"top5={df.loc[df['top_k']==5,'recall@1'].iloc[0]}")

    md = topk_result.markdown_table()
    check("Top-k 消融可输出 Markdown", bool(md.strip()) and "top_k" in md,
          md.strip().splitlines()[0][:50])

    # 分块策略消融：两个策略，索引都存在
    chunk_result = RetrievalEvalResult(testset_path="memory", chunk_strategy="对比")
    chunk_result.results["fixed"] = _make_result("fixed+hybrid_rerank", 3, 2.0)
    chunk_result.results["recursive"] = _make_result("recursive+hybrid_rerank", 1, 2.1)
    cdf = chunk_result.summary_frame()
    check("分块消融表含两个策略", len(cdf) == 2, str(list(cdf["strategy"])))

    # 导出
    tmp_out = Path(tempfile.mkdtemp(prefix="abl_"))
    try:
        written = ab.save_ablation_results(chunk_result, topk_result, tmp_out)
        for key in ("chunk_csv", "chunk_md", "rerank_csv", "rerank_md"):
            check(f"导出 {key}", key in written and written[key].exists(),
                  written[key].name if key in written else "缺失")
        content = written["rerank_md"].read_text(encoding="utf-8")
        check("消融 Markdown 含标题与表格",
              "Rerank Top-k 消融" in content and "recall@1" in content)
        content2 = written["chunk_md"].read_text(encoding="utf-8")
        check("分块消融 Markdown 内容正确", "分块策略消融" in content2)
    finally:
        shutil.rmtree(tmp_out, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="阶段 5 评估模块自测")
    parser.parse_args()

    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        if hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass

    print("=" * 94)
    print(" 阶段 5 评估模块自测（生成与裁判用 mock，不消耗 LLM token）")
    print("=" * 94)

    test_metrics()
    test_testset()
    test_retrieval_eval()
    test_judge()
    test_generation_eval()
    test_visualize()
    test_ablation()

    print("\n" + "=" * 94)
    print(f" 合计 {len(PASSED) + len(FAILED)} 项，通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print(" 失败明细：")
        for name, detail in FAILED:
            print(f"   - {name}: {detail}")
    print("=" * 94)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
