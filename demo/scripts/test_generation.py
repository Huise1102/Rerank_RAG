"""阶段 3 —— 生成模块自测（注入 mock OpenAI 客户端，无需真实 API Key）。

覆盖 PromptBuilder（上下文格式、预算裁剪、多轮历史）、citation（引用解析、编号
映射、越界与拒答识别）、LLMClient（请求参数、重试与超时预算、兜底、降级、流式）。

运行：.venv\\Scripts\\python.exe scripts\\test_generation.py
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import settings  # noqa: E402
from src.generation import citation as cit  # noqa: E402
from src.generation.llm import LLMClient, LLMResponse  # noqa: E402
from src.generation.prompt import (  # noqa: E402
    PromptBuilder,
    PromptContext,
    contexts_from_results,
    select_within_budget,
)

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [OK]   {name}  {detail}")
    else:
        FAILED.append((name, detail))
        print(f"  [FAIL] {name}  {detail}")



class MockCompletions:
    """模拟 chat.completions，记录收到的请求参数。"""

    def __init__(self, owner: "MockClient") -> None:
        self.owner = owner

    def create(self, **params):
        self.owner.calls.append(params)
        model = params.get("model", "")

        # 按模型名失败（用于测试模型降级）
        if model in self.owner.fail_models:
            raise self.owner.fail_error

        # 按次数失败（用于测试重试）
        if self.owner.fail_times > 0:
            self.owner.fail_times -= 1
            raise self.owner.fail_error

        if params.get("stream"):
            return self._stream_chunks()
        text = self.owner.reply_for(model)
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=text, reasoning_content=None),
                finish_reason="stop",
            )],
            usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=80, total_tokens=1280),
        )

    @staticmethod
    def _stream_chunks():
        for piece in ["应在", "规定期限内", "提出", "申请", "[1]。"]:
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=piece), finish_reason=None)]
            )


class MockChat:
    def __init__(self, owner: "MockClient") -> None:
        self.completions = MockCompletions(owner)


class MockClient:
    """最小化的 OpenAI 客户端替身。"""

    def __init__(self, reply: str = "应在规定期限内提出申请[1]。", fail_times: int = 0,
                 fail_error: Exception | None = None,
                 fail_models: set[str] | None = None) -> None:
        self.reply = reply
        self.fail_times = fail_times
        self.fail_error = fail_error or RuntimeError("boom")
        #: 这些模型名一调用就抛错（用于测试降级）
        self.fail_models: set[str] = fail_models or set()
        self.calls: list[dict] = []
        self.chat = MockChat(self)
        self.timeout = 60.0

    def reply_for(self, model: str) -> str:
        if callable(self.reply):
            return self.reply(model)
        return self.reply


def make_contexts(n: int = 3) -> list[PromptContext]:
    return [
        PromptContext(index=i + 1, text=f"这是第 {i + 1} 条参考资料的内容，包含若干条款说明。",
                      source="测试.pdf", page=10 + i, score=0.9 - i * 0.1, chunk_id=f"c{i + 1}")
        for i in range(n)
    ]



def test_prompt() -> None:
    print("\n[1/3] PromptBuilder")
    b = PromptBuilder()
    contexts = make_contexts(3)
    built = b.build("适用范围是怎么规定的？", contexts)

    check("消息结构正确", len(built.messages) == 2 and built.messages[0]["role"] == "system"
          and built.messages[1]["role"] == "user", f"{[m['role'] for m in built.messages]}")

    sys_prompt = built.messages[0]["content"]
    check("系统提示词含'仅基于上下文'要求", "只能依据" in sys_prompt or "仅" in sys_prompt,
          sys_prompt[:40].replace("\n", " "))
    check("系统提示词含引用标注要求", "[编号]" in sys_prompt or "[1]" in sys_prompt)
    check("系统提示词含'资料不足则说明'", "无法回答" in sys_prompt)

    user = built.messages[1]["content"]
    check("用户提示词含问题", "适用范围是怎么规定的" in user)
    # 上下文格式：[编号] 来源：xxx 页码：xxx
    check("上下文带 [编号] 来源 页码", "[1] 来源：测试.pdf 页码：10" in user,
          [ln for ln in user.split("\n") if ln.startswith("[1]")])
    check("三条上下文都编号", all(f"[{i}] 来源：" in user for i in (1, 2, 3)))
    check("used_chars 已统计", built.used_chars > 0, f"{built.used_chars} 字符")

    # 上下文顺序必须稳定（编号即映射依据）
    check("上下文顺序保持", [c.index for c in built.contexts] == [1, 2, 3])

    # 预算裁剪：构造的上下文总长必须确实超过预算，否则判据不成立
    long_ctxs = [
        PromptContext(index=i + 1, text="条款内容" * 120, source="测试.pdf", page=i + 1,
                      chunk_id=f"L{i + 1}")
        for i in range(10)
    ]
    tight_budget = 1500
    total_len = sum(len(c.text) for c in long_ctxs)
    tiny = PromptBuilder(context_max_chars=tight_budget)
    small_built = tiny.build("问题", long_ctxs)
    check("预算裁剪生效",
          len(small_built.contexts) < len(long_ctxs) and small_built.dropped > 0
          and small_built.used_chars <= tight_budget,
          f"上下文总长 {total_len} -> 预算 {tight_budget}，"
          f"保留 {len(small_built.contexts)}/{len(long_ctxs)} 条，丢 {small_built.dropped} 条，"
          f"用 {small_built.used_chars} 字符")
    check("裁剪后编号仍连续（引用映射不失真）",
          [c.index for c in small_built.contexts] == list(range(1, len(small_built.contexts) + 1)),
          str([c.index for c in small_built.contexts]))

    long_ctx = [PromptContext(index=1, text="长" * 3000, source="a.pdf", page=1)]
    kept, dropped = select_within_budget(long_ctx, max_chars=1000)
    check("超长单条被截断而非丢弃", len(kept) == 1 and len(kept[0].text) < 3000,
          f"截断后 {len(kept[0].text)} 字符")

    hist = [("user", "旧问题"), ("assistant", "旧回答")]
    with_hist = b.build("新问题", contexts, history=hist)
    roles = [m["role"] for m in with_hist.messages]
    check("多轮历史注入正确", roles == ["system", "user", "assistant", "user"], str(roles))

    many_hist = [("user", f"q{i}") for i in range(20)] + [("assistant", f"a{i}") for i in range(20)]
    capped = b.build("问题", contexts, history=many_hist)
    max_msgs = 1 + settings.generation.history_max_turns * 2 + 1
    check("历史轮数受配置限制", len(capped.messages) <= max_msgs,
          f"{len(capped.messages)} 条消息（上限 {max_msgs}）")

    # citation_style=none 只应返回基础提示词；不能断言"不含 [编号]"，
    # 因为基础提示词本身就要求用 [编号] 标注来源。
    base_prompt = settings.generation.system_prompt
    none_prompt = PromptBuilder(citation_style="none").build("q", contexts).messages[0]["content"]
    bracket_prompt = PromptBuilder(citation_style="bracket").build("q", contexts).messages[0]["content"]
    check("citation_style=none 不追加引用格式要求", none_prompt == base_prompt,
          "与基础提示词完全一致")
    check("citation_style=bracket 追加了引用格式要求",
          bracket_prompt != base_prompt and "[编号]" in bracket_prompt,
          f"追加 {len(bracket_prompt) - len(base_prompt)} 字符")
    try:
        PromptBuilder(citation_style="乱写")
        check("非法引用风格被拒绝", False, "未抛异常")
    except ValueError:
        check("非法引用风格被拒绝", True)

    dicts = [{"text": "文本", "source": "x.pdf", "page": 3, "final_score": 0.5, "chunk_id": "k"}]
    objs = [SimpleNamespace(text="文本", source="x.pdf", page=3, final_score=0.5,
                            chunk_id="k", metadata={"page": 3})]
    check("contexts_from_results 支持字典", contexts_from_results(dicts)[0].source == "x.pdf")
    check("contexts_from_results 支持对象", contexts_from_results(objs)[0].page == 3)



def test_citation() -> None:
    print("\n[2/3] citation 引用解析")
    contexts = make_contexts(3)

    cases = [
        ("应在规定期限内提出申请[1]。", [1], "标准写法"),
        ("应在规定期限内提出申请[1][2]。", [1, 2], "连续标记"),
        ("相关内容见[1,2]。", [1, 2], "逗号分隔"),
        ("相关内容见[1、2]。", [1, 2], "顿号分隔"),
        ("范围引用[1-3]。", [1, 2, 3], "区间写法"),
        ("全角括号【1】。", [1], "全角方括号"),
        ("（1）圆括号。", [1], "圆括号"),
        ("无引用的普通回答。", [], "无引用"),
    ]
    for text, expect, desc in cases:
        got, _ = cit.parse_markers(text, whitelist={1, 2, 3})
        check(f"解析：{desc}", got == expect, f"{text!r} -> {got}")

    # 映射回 source/page
    report = cit.extract_citations("结论如下[1]，另有补充[3]。", contexts)
    check("引用映射到正确上下文", [c.index for c in report.citations] == [1, 3],
          str([c.label() for c in report.citations]))
    check("引用带 source 与 page", report.citations[0].source == "测试.pdf"
          and report.citations[0].page == 10, report.citations[0].label())
    check("引用带 chunk_id", report.citations[0].chunk_id == "c1")
    check("未引用上下文被记录", [c.index for c in report.unused_contexts] == [2],
          str([c.index for c in report.unused_contexts]))

    # 引用顺序按出现顺序而非编号大小
    rev = cit.extract_citations("先[3]后[1]。", contexts)
    check("引用按出现顺序排列", [c.index for c in rev.citations] == [3, 1],
          str([c.index for c in rev.citations]))

    # 越界引用
    bad = cit.extract_citations("依据[5]可知。", contexts)
    check("越界引用被识别", bad.invalid_indices == [5] and not bad.citations,
          f"invalid={bad.invalid_indices}")
    check("越界引用出现在 summary", "越界" in bad.summary(), bad.summary())

    dup = cit.extract_citations("[1]说了A，[1]又说了B，还有[2]。", contexts)
    counts = {c.index: c.count for c in dup.citations}
    check("重复引用计数正确", counts.get(1) == 2 and counts.get(2) == 1, str(counts))

    # 拒答识别
    for text in ["根据现有资料无法回答该问题。", "资料中未提及相关内容。",
                 "抱歉，我无法确定该信息。", "上下文里没有找到相关依据。"]:
        check(f"拒答识别：{text[:12]}…", cit.is_refusal(text))
    check("正常回答不被判为拒答", not cit.is_refusal("应在规定期限内提出申请[1]。"))
    refusal_report = cit.extract_citations("根据现有资料无法回答该问题。", contexts)
    check("拒答答案的引用报告正确",
          refusal_report.is_refusal and not refusal_report.citations,
          refusal_report.summary())

    # strip / renumber
    check("strip_markers 去标记", cit.strip_markers("结论[1]与[2]。") == "结论与。",
          cit.strip_markers("结论[1]与[2]。"))
    check("renumber 重编号", cit.renumber("见[1]和[2]。", {1: 10, 2: 20}) == "见[10]和[20]。",
          cit.renumber("见[1]和[2]。", {1: 10, 2: 20}))

    # sources 去重聚合
    multi = cit.extract_citations("[1][2][3]", contexts)
    check("sources 聚合正常", len(multi.sources()) >= 1, str(multi.sources()[:1]))
    check("coverage 计算正确", abs(multi.coverage - 1.0) < 1e-6, f"{multi.coverage}")

    # 防御：超大区间不应爆内存
    huge, _ = cit.parse_markers("[1-9999]", whitelist=set(range(1, 10)))
    check("超大区间有上限保护", len(huge) <= 60, f"{len(huge)} 个编号")

    import json

    json.dumps(report.to_dict(), ensure_ascii=False)
    check("引用报告可 JSON 序列化", True)



def test_llm() -> None:
    print("\n[3/3] LLMClient")
    contexts = make_contexts(3)
    mock = MockClient(reply="应在规定期限内提出申请[1]，另见[2]。")
    client = LLMClient(client=mock)
    resp = client.generate("适用范围是怎么规定的？", contexts)

    check("返回 answer", "规定期限内" in resp.answer, resp.answer)
    check("返回使用的 contexts", len(resp.contexts) == 3, f"{len(resp.contexts)} 条")
    check("记录 usage", resp.usage.get("total_tokens") == 1280, str(resp.usage))
    check("返回类型为 LLMResponse", isinstance(resp, LLMResponse))
    check("引用报告可用", [c.index for c in resp.citations.citations] == [1, 2],
          resp.citations.summary())

    sent = mock.calls[-1]
    check("调用参数含 model/temperature/max_tokens",
          sent["model"] == settings.llm.model and "temperature" in sent and "max_tokens" in sent,
          f"model={sent['model']} temp={sent['temperature']}")
    check("非流式调用", sent["stream"] is False)
    check("messages 已传入", len(sent["messages"]) == 2)

    # base_url 拼接约定：存根地址，不能带 /chat/completions
    check("base_url 为根地址", not client.base_url.endswith("/chat/completions"), client.base_url)
    mock2 = MockClient()
    c2 = LLMClient(client=mock2)
    empty = c2.generate("问题", [])
    check("无上下文时返回兜底话术", empty.answer == settings.generation.no_context_reply,
          empty.answer[:30])
    check("无上下文时不调用 LLM", len(mock2.calls) == 0, f"{len(mock2.calls)} 次调用")
    check("无上下文时给出告警", bool(empty.warnings), str(empty.warnings))
    class TimeoutLike(Exception):
        pass

    TimeoutLike.__name__ = "APITimeoutError"

    mock3 = MockClient(fail_times=2, fail_error=TimeoutLike("模拟超时"))
    client3 = LLMClient(client=mock3, max_retries=3, timeout=60)
    t0 = time.perf_counter()
    resp3 = client3.generate("问题", contexts)
    dt = time.perf_counter() - t0
    check("超时可重试并最终成功", "规定期限内" in resp3.answer or resp3.answer,
          f"调用 {len(mock3.calls)} 次，耗时 {dt:.1f}s")
    check("重试次数符合预期", len(mock3.calls) == 3, f"{len(mock3.calls)} 次")
    class BadRequest(Exception):
        pass

    BadRequest.__name__ = "BadRequestError"
    mock4 = MockClient(fail_times=5, fail_error=BadRequest("参数错误"))
    try:
        LLMClient(client=mock4, max_retries=3).generate("问题", contexts)
        check("非可重试异常立即抛出", False, "未抛异常")
    except BadRequest:
        check("非可重试异常立即抛出", True, f"仅调用 {len(mock4.calls)} 次")
    except Exception as exc:  # noqa: BLE001
        check("非可重试异常立即抛出", False, f"抛出了 {type(exc).__name__}")
    mock5 = MockClient(fail_times=99, fail_error=TimeoutLike("一直超时"))
    client5 = LLMClient(client=mock5, max_retries=5, timeout=3)
    t0 = time.perf_counter()
    try:
        client5.generate("问题", contexts)
        check("超时预算用尽后放弃", False, "未抛异常")
    except Exception as exc:  # noqa: BLE001
        dt = time.perf_counter() - t0
        check("超时预算用尽后放弃", dt < 12, f"{type(exc).__name__}，耗时 {dt:.1f}s")
    # 显式指定 model 时必须 fail-fast，不能悄悄换模型（否则模型对比实验不可信）
    mock6 = MockClient(fail_times=1, fail_error=BadRequest("主模型不可用"))
    try:
        LLMClient(client=mock6, model=settings.llm.model).generate(
            "问题", contexts, use_fallback_models=True,
        )
        check("显式指定模型时不再降级", False, "未抛异常，说明发生了静默降级")
    except BadRequest:
        check("显式指定模型时不再降级", True, f"调用 {len(mock6.calls)} 次后按原异常抛出")
    except Exception as exc:  # noqa: BLE001
        check("显式指定模型时不再降级", False, f"抛出了 {type(exc).__name__}")

    # 仅未指定 model 且配置了备用模型时才允许降级；须用 fail_models 让"只有主模型
    # 失败"，否则按次数失败会连备用模型一起失败，测的就不是降级路径。
    original_fallbacks = list(settings.llm.fallback_models)
    try:
        object.__setattr__(settings.llm, "fallback_models", ["deepseek-reasoner"])
        mock7 = MockClient(fail_models={settings.llm.model}, fail_error=TimeoutLike("主模型超时"))
        resp7 = LLMClient(client=mock7).generate("问题", contexts, use_fallback_models=True)
        used = [c["model"] for c in mock7.calls]
        check("未指定模型时允许降级到备用模型", resp7.model == "deepseek-reasoner",
              f"调用序列={used}，最终模型={resp7.model}")
    finally:
        object.__setattr__(settings.llm, "fallback_models", original_fallbacks)
    mock8 = MockClient()
    LLMClient(client=mock8).generate("问题", contexts, model="deepseek-chat",
                                     temperature=0.7, max_tokens=512)
    last = mock8.calls[-1]
    check("可覆盖 temperature/max_tokens",
          last["temperature"] == 0.7 and last["max_tokens"] == 512,
          f"temp={last['temperature']} max_tokens={last['max_tokens']}")
    mock9 = MockClient()
    client9 = LLMClient(client=mock9)
    pieces = list(client9.stream("适用范围？", contexts))
    joined = "".join(pieces)
    check("流式生成逐段返回", len(pieces) > 1, f"{len(pieces)} 段")
    check("流式内容完整", "规定期限内" in joined, joined)
    check("流式请求参数正确", mock9.calls[-1]["stream"] is True)

    mock10 = MockClient()
    client10 = LLMClient(client=mock10)
    stream, box = client10.stream_response("适用范围？", contexts)
    list(stream)
    resp10 = box["response"]
    check("stream_response 返回完整结果", resp10 is not None and "规定期限内" in resp10.answer,
          resp10.summary() if resp10 else "")
    check("流式也解析引用", resp10 is not None and resp10.citations.has_citation,
          resp10.citations.summary() if resp10 else "")
    check("记录首 token 延迟", resp10 is not None and resp10.first_token_seconds is not None,
          f"{resp10.first_token_seconds:.3f}s" if resp10 else "")
    ready, why = LLMClient(client=MockClient()).is_ready()
    check("is_ready 反映配置状态", isinstance(ready, bool), why)
    try:
        from src.retrieval.pipeline import retrieve

        retrieved = retrieve("学校规章制度的适用范围", strategy="hybrid_rerank",
                             chunk_strategy="recursive", write_log=False)
        mock11 = MockClient(reply="应在规定期限内提出申请[1]。")
        e2e = LLMClient(client=mock11).generate("学校规章制度的适用范围", retrieved.results)
        check("端到端：检索结果可直接喂给生成", len(e2e.contexts) == len(retrieved.results),
              f"{len(e2e.contexts)} 条上下文")
        check("端到端：prompt 中的来源正确",
              "测试.pdf" in mock11.calls[-1]["messages"][1]["content"],
              "上下文含来源信息")
        check("端到端：引用能溯源到页码",
              e2e.citations.citations[0].page != "" if e2e.citations.citations else False,
              e2e.citations.summary())
    except Exception as exc:  # noqa: BLE001
        check("端到端联调", False, f"{type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="阶段 3 生成模块自测")
    parser.parse_args()

    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        if hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass

    print("=" * 92)
    print(" 阶段 3 生成模块自测（mock LLM，无需 API Key）")
    print("=" * 92)

    test_prompt()
    test_citation()
    test_llm()

    print("\n" + "=" * 92)
    print(f" 合计 {len(PASSED) + len(FAILED)} 项，通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print(" 失败明细：")
        for name, detail in FAILED:
            print(f"   - {name}: {detail}")
    print("=" * 92)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
