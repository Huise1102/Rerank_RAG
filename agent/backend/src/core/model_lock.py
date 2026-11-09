"""大模型加载专用全局锁。

transformers 与 sentence-transformers 的权重加载不是线程安全的，
并发加载会拿到 meta 设备上的参数并抛出 NotImplementedError。
所有大模型构造都需经过这把锁串行执行。
"""

from __future__ import annotations

import threading

# 保护 CrossEncoder、SentenceTransformer 与 FlagReranker 的构造过程
MODEL_LOAD_LOCK = threading.Lock()
