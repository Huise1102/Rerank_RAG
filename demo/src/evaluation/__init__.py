"""阶段 5 —— 评估模块。

testset.py 测试集加载与校验（只做加载校验，标注由人工完成），
metrics.py 检索指标，retrieval_eval.py 多策略检索对比，
judge.py LLM-as-Judge 打分，generation_eval.py 生成质量评估，
ablation.py 消融，visualize.py 对比图，cli.py 命令行入口。

快速开始::

    python -m src.evaluation.cli candidates             # 生成标注候选
    # （人工标注 data/testset.json）
    python -m src.evaluation.cli check                  # 校验
    python -m src.evaluation.cli retrieval              # 检索对比（不耗 token）
    python -m src.evaluation.cli generation --limit 3   # 生成质量（耗 token）
    python -m src.evaluation.cli ablation               # 消融
    python -m src.evaluation.cli all                    # 一键：检索+消融+出图
"""

from __future__ import annotations

__all__ = [
    "CATEGORIES",
    "TestItem",
    "TestSet",
    "load_testset",
    "save_testset",
]


def __getattr__(name: str):
    """惰性暴露常用对象，避免导入包时就加载 pandas / matplotlib 等重依赖。"""
    if name in __all__:
        from src.evaluation import testset as _t

        return getattr(_t, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
