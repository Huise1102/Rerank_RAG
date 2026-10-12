"""``main.py`` 的别名模块，使 ``uvicorn src.api.main:app`` 与 ``uvicorn src.api.app:app``
两种启动方式都可用。

应用实现全在 ``main.py``，这里不重复定义，避免出现"两个 app 对象、状态各一份"。
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
