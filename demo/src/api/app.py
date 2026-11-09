"""转发 main.py 的别名模块，使按 main 或 app 两种模块路径都能启动服务。

应用实现集中在 main.py，这里只做导入，避免产生两个应用对象。
"""

from __future__ import annotations

from src.api.main import (
    RETRIEVAL_STRATEGIES,
    __version__,
    app,
    create_app,
    main,
    router,
)

__all__ = ["app", "create_app", "main", "router", "RETRIEVAL_STRATEGIES", "__version__"]

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
