"""大模型加载专用全局锁。

transformers / sentence-transformers 的权重加载不是线程安全的：两个线程同时
``from_pretrained`` 时，后加载的模型会拿到 meta 设备上的参数，随后 ``Module.to()``
抛 ``NotImplementedError: Cannot copy out of meta tensor``。

实测（transformers 4.57.6 + sentence-transformers 3.4.1）：预热线程加载精排模型
（CrossEncoder）与入库线程加载 embedding 模型（SentenceTransformer）并发时
必然复现；串行执行则两者都正常。

因此所有大模型构造都必须经过这把锁，串行执行。
"""

from __future__ import annotations

import threading

#: 保护 CrossEncoder / SentenceTransformer / FlagReranker 的构造过程
MODEL_LOAD_LOCK = threading.Lock()
