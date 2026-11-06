#!/usr/bin/env python
"""一键启动四个项目（demo / product / agent / qwen_rag）。

端口约定：
    demo     后端 8001 + 前端 5171（Streamlit）
    product  后端 8002 + 前端 5172（Vite）
    agent    后端 8003 + 前端 5173（Vite）
    qwen_rag 后端 8004 + 前端 5174（Vite，依赖 Ollama）

用法：
    python start_all.py                        # 启动 Ollama + 八个进程
    python start_all.py --only agent           # 只启动 agent（后端 + 前端）
    python start_all.py --only demo product    # 启动指定多个项目
    python start_all.py --dry-run              # 只检查环境、依赖和端口，不启动

启动后按 Ctrl+C 会停止本次脚本拉起的全部进程；已在运行的端口会被跳过。
运行日志写入 .run/logs/<服务名>.log。
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / ".run" / "logs"
OLLAMA_PORT = 11434
OLLAMA_TAGS_URL = f"http://127.0.0.1:{OLLAMA_PORT}/api/tags"


def say(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def port_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def venv_python(relative_dir: str) -> str | None:
    """返回项目内 .venv 的解释器路径；不存在则返回 None。"""
    base = ROOT / relative_dir / ".venv"
    exe = base / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(exe) if exe.exists() else None


def npm_argv(*args: str) -> list[str] | None:
    """构造 npm 命令。

    Windows 下 npm 是 npm.cmd，直接交给 cmd 会因 ``C:\\Program Files`` 中的空格
    解析失败；优先用 node 执行 npm-cli.js，避免命令行引号问题。
    """
    npm = shutil.which("npm")
    if not npm:
        return None
    if os.name == "nt" and npm.lower().endswith((".cmd", ".bat")):
        node = shutil.which("node")
        npm_cli = Path(npm).resolve().parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
        if node and npm_cli.exists():
            return [node, str(npm_cli), *args]
        return [os.environ.get("ComSpec", "cmd.exe"), "/d", "/c", npm, *args]
    return [npm, *args]


@dataclass
class Project:
    name: str
    backend_dir: str
    backend_port: int
    frontend_dir: str
    frontend_port: int
    python: str
    frontend_argv: list[str]
    requires_node_modules: bool = True
    backend_timeout: int = 300
    frontend_timeout: int = 90


@dataclass
class Running:
    label: str
    process: subprocess.Popen
    log_handle: object
    log_path: Path


class Launcher:
    """管理本次脚本拉起的进程，退出时统一按进程树停止。"""

    def __init__(self) -> None:
        self.running: list[Running] = []

    def spawn(self, label: str, argv: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f"{label}.log"
        handle = open(log_path, "wb")
        kwargs: dict[str, object] = {
            "cwd": str(cwd),
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": handle,
            "stderr": subprocess.STDOUT,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(argv, **kwargs)
        except Exception:
            handle.close()
            raise
        self.running.append(Running(label, proc, handle, log_path))
        say(f"  启动 {label}（PID {proc.pid}）→ {log_path.relative_to(ROOT)}")
        return proc

    def wait(self, proc: subprocess.Popen, url: str, timeout: int) -> tuple[bool, str]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            code = proc.poll()
            if code is not None:
                return False, f"进程提前退出（退出码 {code}）"
            if http_ok(url):
                return True, "ok"
            time.sleep(1.5)
        return False, f"等待超时（{timeout}s）"

    def stop_all(self) -> None:
        for item in reversed(self.running):
            proc = item.process
            if proc.poll() is None:
                say(f"  停止 {item.label}（PID {proc.pid}）")
                try:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                            capture_output=True,
                            check=False,
                        )
                    else:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except Exception as exc:  # noqa: BLE001
                    say(f"  停止 {item.label} 失败：{exc}")
            try:
                item.log_handle.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        self.running.clear()


def build_projects() -> list[Project]:
    demo_python = venv_python("demo") or sys.executable
    shared_python = venv_python("product/backend") or demo_python
    npm = npm_argv("run", "dev")
    if npm is None:
        npm = []

    return [
        Project(
            name="demo",
            backend_dir="demo",
            backend_port=8001,
            frontend_dir="demo",
            frontend_port=5171,
            python=demo_python,
            frontend_argv=[demo_python, "-m", "streamlit", "run", "app.py"],
            requires_node_modules=False,
        ),
        Project(
            name="product",
            backend_dir="product/backend",
            backend_port=8002,
            frontend_dir="product/frontend",
            frontend_port=5172,
            python=shared_python,
            frontend_argv=npm,
        ),
        Project(
            name="agent",
            backend_dir="agent/backend",
            backend_port=8003,
            frontend_dir="agent/frontend",
            frontend_port=5173,
            python=shared_python,
            frontend_argv=npm,
        ),
        Project(
            name="qwen_rag",
            backend_dir="qwen_rag/backend",
            backend_port=8004,
            frontend_dir="qwen_rag/frontend",
            frontend_port=5174,
            python=shared_python,
            frontend_argv=npm,
        ),
    ]


def preflight(projects: list[Project]) -> list[str]:
    problems: list[str] = []
    for project in projects:
        backend_dir = ROOT / project.backend_dir
        frontend_dir = ROOT / project.frontend_dir
        backend_main = backend_dir / "src" / "api" / "main.py"
        if not backend_main.exists():
            problems.append(f"{project.name}: 缺少后端入口 {backend_main}")
        if not Path(project.python).exists():
            problems.append(f"{project.name}: 找不到 Python 解释器 {project.python}")
        if not frontend_dir.exists():
            problems.append(f"{project.name}: 缺少前端目录 {frontend_dir}")
        if project.requires_node_modules and not (frontend_dir / "node_modules").exists():
            problems.append(f"{project.name}: 缺少 node_modules，请先在 {frontend_dir} 执行 npm install")
        if project.requires_node_modules and not project.frontend_argv:
            problems.append(f"{project.name}: 未找到 npm，请安装 Node.js")
        if project.name == "demo" and not (frontend_dir / "app.py").exists():
            problems.append("demo: 缺少 Streamlit 入口 app.py")

    if not port_in_use(3306):
        problems.append("MySQL 3306 未监听：四个后端都需要数据库")
    if not port_in_use(6379):
        say("提示：Redis 6379 未监听，后端会走缓存降级路径")
    return problems


def ensure_ollama(launcher: Launcher, timeout: int = 90) -> bool:
    if port_in_use(OLLAMA_PORT):
        say(f"Ollama 已在运行（{OLLAMA_PORT}），跳过启动")
        return True

    exe = shutil.which("ollama")
    if not exe:
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
            Path(r"C:\Users\98429\AppData\Local\Programs\Ollama\ollama.exe"),
        ]
        exe = next((str(p) for p in candidates if p.exists()), None)

    if not exe:
        say("警告：未找到 ollama，qwen_rag 后端将无法回答")
        return False

    env = os.environ.copy()
    models = Path(os.environ.get("OLLAMA_MODELS", r"D:\98429\ollama\models"))
    if "OLLAMA_MODELS" not in env and models.is_dir():
        env["OLLAMA_MODELS"] = str(models)
    env.setdefault("PYTHONIOENCODING", "utf-8")

    proc = launcher.spawn("ollama", [exe, "serve"], ROOT, env)
    ok, reason = launcher.wait(proc, OLLAMA_TAGS_URL, timeout)
    if ok:
        say("Ollama 已就绪")
    else:
        say(f"警告：Ollama 未就绪（{reason}），请查看 .run/logs/ollama.log")
    return ok


def start_project(project: Project, launcher: Launcher) -> bool:
    say(f"[{project.name}] 后端 {project.backend_port} / 前端 {project.frontend_port}")
    backend_ok = False

    backend_url = f"http://127.0.0.1:{project.backend_port}/health"
    if port_in_use(project.backend_port):
        say(f"  后端端口 {project.backend_port} 已在监听，跳过启动")
        backend_ok = True
    else:
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUNBUFFERED", "1")
        try:
            proc = launcher.spawn(
                f"{project.name}-backend",
                [project.python, "-m", "src.api.main"],
                ROOT / project.backend_dir,
                env,
            )
        except Exception as exc:  # noqa: BLE001
            say(f"  ✗ 后端启动失败：{exc}")
            proc = None
        if proc is not None:
            backend_ok, reason = launcher.wait(proc, backend_url, project.backend_timeout)
            if backend_ok:
                say(f"  ✓ 后端就绪 {backend_url}")
            else:
                say(f"  ✗ 后端未就绪：{reason}（日志 {project.name}-backend.log）")

    frontend_url = f"http://127.0.0.1:{project.frontend_port}/"
    if port_in_use(project.frontend_port):
        say(f"  前端端口 {project.frontend_port} 已在监听，跳过启动")
    elif not project.frontend_argv:
        say("  ✗ 未找到 npm，跳过前端")
    else:
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            proc = launcher.spawn(
                f"{project.name}-frontend",
                project.frontend_argv,
                ROOT / project.frontend_dir,
                env,
            )
            ok, reason = launcher.wait(proc, frontend_url, project.frontend_timeout)
            if ok:
                say(f"  ✓ 前端就绪 {frontend_url}")
            else:
                say(f"  ✗ 前端未就绪：{reason}（日志 {project.name}-frontend.log）")
        except Exception as exc:  # noqa: BLE001
            say(f"  ✗ 前端启动失败：{exc}")

    return backend_ok


def main() -> int:
    parser = argparse.ArgumentParser(description="一键启动 demo / product / agent / qwen_rag")
    parser.add_argument("--dry-run", action="store_true", help="只检查环境与端口，不启动进程")
    parser.add_argument(
        "--only",
        nargs="+",
        choices=["demo", "product", "agent", "qwen_rag"],
        metavar="NAME",
        help="只启动指定项目（可写多个，空格分隔）",
    )
    args = parser.parse_args()

    all_projects = build_projects()
    projects = [p for p in all_projects if not args.only or p.name in args.only]

    say("端口规划：")
    for project in projects:
        say(f"  {project.name:<9} 后端 {project.backend_port}  前端 {project.frontend_port}")

    problems = preflight(projects)
    if problems:
        say("环境检查发现问题：")
        for problem in problems:
            say(f"  - {problem}")
        if args.dry_run:
            return 1
        say("继续尝试启动（失败的服务请查看 .run/logs）")
    else:
        say("环境检查通过")

    if args.dry_run:
        for project in projects:
            state = []
            state.append(f"后端 {project.backend_port} " + ("占用" if port_in_use(project.backend_port) else "空闲"))
            state.append(f"前端 {project.frontend_port} " + ("占用" if port_in_use(project.frontend_port) else "空闲"))
            say(f"  {project.name:<9} " + "，".join(state))
        say("dry-run 结束，未启动任何进程")
        return 0 if not problems else 1

    launcher = Launcher()
    try:
        if any(p.name == "qwen_rag" for p in projects):
            ensure_ollama(launcher)
        for project in projects:
            start_project(project, launcher)

        say("=" * 62)
        say("访问地址：")
        for project in projects:
            say(f"  {project.name:<9} http://localhost:{project.frontend_port}  (API {project.backend_port})")
        say("日志目录：.run/logs")
        say("按 Ctrl+C 停止本次启动的全部进程")
        say("=" * 62)

        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        say("收到 Ctrl+C，开始停止服务")
        return 130
    except Exception as exc:  # noqa: BLE001
        say(f"启动异常：{exc}")
        return 1
    finally:
        launcher.stop_all()
        say("已停止本次启动的进程")


if __name__ == "__main__":
    raise SystemExit(main())
