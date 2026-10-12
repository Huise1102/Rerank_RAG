"""RAG 领域问答系统 —— 源码包。

ingest/ 数据入库，retrieval/ 检索与精排，generation/ 生成，
evaluation/ 评估，api/ FastAPI 服务，utils/ 通用工具。

约定：所有模块统一从 `config import settings` 读取配置，不硬编码参数。
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
