import sys

import httpx

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8004"


def main() -> int:
    try:
        response = httpx.get(f"{BASE_URL}/health", timeout=5)
        response.raise_for_status()
        print(f"健康检查通过：{response.json()}")
        return 0
    except Exception as exc:
        print(f"健康检查失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
