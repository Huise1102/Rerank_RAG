"""评估模块。

testset.py 加载与校验测试集，metrics.py 计算检索指标，retrieval_eval.py 对比多种检索策略，judge.py 做大模型打分，generation_eval.py 评估生成质量，ablation.py 做消融实验，visualize.py 出对比图，cli.py 提供命令行入口。
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
    """按需暴露常用对象，避免导入包时加载 pandas 与 matplotlib 等重依赖。"""
    if name in __all__:
        from src.evaluation import testset as _t

        return getattr(_t, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
