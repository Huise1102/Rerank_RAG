"""大模型加载专用全局锁。

transformers 与 sentence-transformers 的权重加载不是线程安全的：
两个线程同时加载模型时，后加载的模型会拿到错误设备上的参数并在搬迁时报错。
所有大模型构造都经过这把锁串行执行。
"""

from __future__ import annotations

import threading

# 保护 CrossEncoder、SentenceTransformer 与 FlagReranker 的构造过程
MODEL_LOAD_LOCK = threading.Lock()
