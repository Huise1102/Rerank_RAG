"""Streamlit 前端：经 HTTP 调用 FastAPI 后端的问答与入库接口。
视觉全部由 ``inject_theme()`` 注入的 CSS 覆盖原生组件实现，是样式的唯一来源。
前端不做检索，只渲染回答、引用与调试面板。
"""

from __future__ import annotations

import html
import json
import re
import sys


def _setup_io_encoding() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


_setup_io_encoding()

import time  # noqa: E402
from typing import Any, Iterator  # noqa: E402

import requests  # noqa: E402
import streamlit as st  # noqa: E402

from config import settings  # noqa: E402


#: 配色总表
#:   背景 #fbf8f2 ｜ 侧边栏 #f5f0e6 ｜ 卡片 #faf8f2 ｜ 分隔线 #e0d8c8 ｜ 文字 #2c2c2c
#:   次要 #6b5f3f ｜ 标签 #8a7a5a ｜ 强调 #8b6f47 ｜ 正常 #5a7a4a ｜ 错误 #a02828
#:   开关开启色 #3b7dd8（唯一有意偏离主色调的颜色，见下方 st.toggle 规则）
#: 约束：不用渐变、不用毛玻璃、圆角 ≤4px、不用 emoji；字体链优先思源宋体，回退系统宋体。
#: 不要给 `span` / `div` 写通配 font-family：Streamlit 图标靠「图标名 + 连字字体」
#: 显示，通配规则会抢掉连字字体，页面直接印出 `keyboard_arrow_down` 等图标名。
_CSS = """
/* ================= 基础：浅色米白，无渐变无毛玻璃 ================= */
html, body, .stApp {
    background-color: #fbf8f2 !important;
    background-image: none !important;
    color: #2c2c2c;
}
header[data-testid="stHeader"] { background: transparent; }
[data-testid="stDecoration"] { display: none; }
[data-testid="stToolbar"] { right: .6rem; }

/* ================= 字体：正文容器靠继承传递 ================= */
.stApp, .stApp p, .stApp li, .stApp label {
    font-family: "Source Han Serif SC", "Noto Serif CJK SC", "Songti SC", serif;
}
/* 表单控件不继承字体，需显式指定（这些元素内部没有图标连字） */
.stApp button, .stApp input, .stApp textarea, .stApp select {
    font-family: "Source Han Serif SC", "Noto Serif CJK SC", "Songti SC", serif;
}

/* ================= 正文：15px / 1.75 / #2c2c2c ================= */
.stApp [data-testid="stMarkdownContainer"] {
    font-size: 15px; line-height: 1.75; color: #2c2c2c;
}
.stApp [data-testid="stMarkdownContainer"] p,
.stApp [data-testid="stMarkdownContainer"] li {
    font-size: 15px; line-height: 1.75; color: #2c2c2c;
}
.stApp [data-testid="stMarkdownContainer"] h1,
.stApp [data-testid="stMarkdownContainer"] h2,
.stApp [data-testid="stMarkdownContainer"] h3 { color: #1a1a1a; }

/* 表格：细边框、无阴影、圆角 4px */
.stApp [data-testid="stMarkdownContainer"] table { border-collapse: collapse; }
.stApp [data-testid="stMarkdownContainer"] th,
.stApp [data-testid="stMarkdownContainer"] td {
    border: 1px solid #e0d8c8; padding: 6px 10px; font-size: 14px;
}
.stApp [data-testid="stMarkdownContainer"] th { background: #faf6ed; color: #4a4a4a; }

/* 分隔线 */
.stApp hr { border-color: #e0d8c8 !important; background-color: #e0d8c8 !important; }
.stApp [data-testid="stDivider"] { background-color: #e0d8c8 !important; }

/* 小字（caption 等）= 标签色 #8a7a5a */
.stApp [data-testid="stCaptionContainer"] p,
.stApp [data-testid="stCaptionContainer"] span,
.stApp [data-testid="stCaptionContainer"] div {
    font-size: 12px !important; line-height: 1.6 !important; color: #8a7a5a !important;
}

/* ================= 顶部标题区（纯文字，不用图片） ================= */
.app-hero {
    border-left: 4px solid #8b6f47;
    padding: 8px 0 14px 16px;
    margin: 0 0 20px 0;
    background: transparent;
    box-shadow: none;
}
.app-hero__title {
    font-size: 1.8rem; font-weight: 700; color: #1a1a1a; line-height: 1.3;
}
.app-hero__sub {
    font-size: .88rem; color: #6b5f3f; margin-top: 4px; line-height: 1.6;
}

/* ================= 对话气泡（自绘，不用 st.chat_message） ================= */
.msg-label {
    font-size: 13px; color: #8a7a5a; line-height: 1.5;
    letter-spacing: .02em; margin: 0 0 4px 0;
}
/* 用户消息：右对齐 div */
.msg-user {
    display: flex; flex-direction: column; align-items: flex-end;
    margin: 16px 0 10px 0;
}
.msg-user__body {
    background: #f5f0e6;
    border: 1px solid #e0d8c8;
    border-radius: 4px;
    padding: 10px 16px;
    max-width: 78%;
    font-size: 15px; line-height: 1.75; color: #2c2c2c;
    white-space: pre-wrap; word-break: break-word;
}
/* AI 消息：keyed container（st-key-ai-*）承载原生 markdown，
   背景透明 + 左侧 3px 竖线 + 内边距 10px 16px */
[class*="st-key-ai-"] {
    background: transparent;
    border-left: 3px solid #8b6f47;
    border-radius: 0;
    padding: 10px 16px;
    margin: 0 0 14px 0;
    box-shadow: none;
}
/* 引用编号 [1] [2]：棕褐色、加粗 */
.md-cite { color: #8b6f47; font-weight: 600; }
/* 流式光标：不用 CSS 动画 */
.cursor { color: #8b6f47; font-weight: 600; }

/* 兜底：即便将来又用回 st.chat_message，也保持同一套外观 */
[data-testid="stChatMessage"] {
    background: #f5f0e6;
    border: 1px solid #e0d8c8;
    border-radius: 4px;
    box-shadow: none;
}

/* ================= 引用溯源区 ================= */
.cite-item { margin: 10px 0 2px 0; }
.cite-idx { color: #8b6f47; font-weight: 600; font-size: 13px; }
.cite-file { font-size: 13px; color: #2c2c2c; }
.cite-meta { font-size: 12px; color: #8a7a5a; }
.cite-text {
    background: #faf8f2;
    border-left: 3px solid #8b6f47;
    border-radius: 0;
    padding: 12px 16px;
    margin: 6px 0 12px 0;
    font-family: "JetBrains Mono", "Cascadia Mono", "Consolas", monospace;
    font-size: 13px; line-height: 1.65; color: #3c3c3c;
    white-space: pre-wrap; word-break: break-word;
}
.halluc-note { font-size: 12px; color: #a02828; margin: 0 0 6px 0; line-height: 1.6; }

/* ================= 检索调试面板 ================= */
.dbg-title {
    font-size: 13px; font-weight: 600; color: #4a4a4a; margin: 2px 0 6px 0;
}
.dbg-doc { font-size: 13px; color: #2c2c2c; margin: 8px 0 0 0; line-height: 1.6; }
.dbg-meta {
    font-family: "JetBrains Mono", "Cascadia Mono", "Consolas", monospace;
    font-size: 12px; color: #6b5f3f; line-height: 1.7; margin: 2px 0 0 0;
}
.dbg-line {
    font-family: "JetBrains Mono", "Cascadia Mono", "Consolas", monospace;
    font-size: 12px; color: #6b5f3f; line-height: 1.9;
}

/* ================= 侧边栏 ================= */
section[data-testid="stSidebar"] {
    background: #f5f0e6;
    border-right: 1px solid #e0d8c8;
    box-shadow: 2px 0 12px rgba(0, 0, 0, .03);
}
section[data-testid="stSidebar"] > div:first-child { width: 18rem !important; }
section[data-testid="stSidebar"] [data-testid="stSidebarContent"] { width: 18rem !important; }

/* 侧边栏小标题（st.markdown "#### 检索策略"）：12px 大写字间距 .05em */
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h4 {
    font-size: 12px !important;
    color: #6b5f3f !important;
    font-weight: 600 !important;
    text-transform: uppercase;
    letter-spacing: .05em;
    margin: 2px 0 6px 0;
}
/* 侧边栏文案统一压到 13px，避免挤高 */
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] li {
    font-size: 13px; line-height: 1.65;
}
/* 侧边栏 caption 12px */
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p,
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] div {
    font-size: 12px !important; color: #8a7a5a !important;
}

/* 折叠区：底色 #faf6ed、边框 1px #e0d8c8、圆角 4px */
section[data-testid="stSidebar"] [data-testid="stExpander"],
.stApp [data-testid="stExpander"] {
    background: #faf6ed;
    border: 1px solid #e0d8c8;
    border-radius: 4px;
    box-shadow: none;
    overflow: hidden;
}
section[data-testid="stSidebar"] [data-testid="stExpander"] { margin-bottom: 8px; }
.stApp [data-testid="stExpander"] summary {
    font-size: 13px; font-weight: 600; color: #4a4a4a;
    padding: 6px 10px; min-height: 2.1rem;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.stApp [data-testid="stExpander"] summary:hover { color: #8b6f47; }
.stApp [data-testid="stExpanderDetails"] { padding: 4px 10px 10px 10px; }

/* 按钮：白底棕褐字，hover 米黄底 + 棕褐边 */
.stApp .stButton > button,
.stApp [data-testid="stBaseButton-secondary"],
.stApp [data-testid="stBaseButton-primary"] {
    background: #ffffff;
    color: #8b6f47;
    border: 1px solid #d0c8b8;
    border-radius: 4px;
    box-shadow: none;
    font-size: 13px;
    font-weight: 600;
    min-height: 2.05rem;
    padding: .3rem .6rem;
}
.stApp .stButton > button:hover,
.stApp .stButton > button:focus,
.stApp [data-testid="stBaseButton-secondary"]:hover,
.stApp [data-testid="stBaseButton-primary"]:hover {
    background: #f0ead8;
    border-color: #8b6f47;
    color: #8b6f47;
}
section[data-testid="stSidebar"] .stButton > button { width: 100%; }

/* 下拉框：白底、#d0c8b8 边框、13px */
.stApp [data-baseweb="select"] > div {
    background: #ffffff;
    border: 1px solid #d0c8b8;
    border-radius: 4px;
    min-height: 2.15rem;
    font-size: 13px;
    color: #2c2c2c;
    box-shadow: none;
}
.stApp [data-baseweb="select"] span,
.stApp [data-baseweb="select"] input { font-size: 13px; color: #2c2c2c; }
.stApp [data-baseweb="select"] svg { color: #6b5f3f; }
/* 下拉弹层 */
[data-baseweb="popover"] [role="listbox"] {
    background: #ffffff; border: 1px solid #e0d8c8; border-radius: 4px;
}
[data-baseweb="popover"] [role="option"] { font-size: 13px; color: #2c2c2c; }
[data-baseweb="popover"] [role="option"]:hover { background: #f0ead8; }
[data-baseweb="popover"] [aria-selected="true"] { background: #f0ead8; color: #8b6f47; }

/* 文件上传：虚线框 1px dashed #c4b898、底 #faf6ed */
.stApp [data-testid="stFileUploaderDropzone"] {
    background: #faf6ed;
    border: 1px dashed #c4b898;
    border-radius: 4px;
    padding: 8px 10px;
}
.stApp [data-testid="stFileUploaderDropzoneInstructions"] span,
.stApp [data-testid="stFileUploaderDropzoneInstructions"] small,
.stApp [data-testid="stFileUploaderDropzone"] small {
    font-size: 12px !important; color: #8a7a5a !important;
}
.stApp [data-testid="stFileUploaderDropzone"] button {
    background: #ffffff; color: #8b6f47;
    border: 1px solid #d0c8b8; border-radius: 4px; font-size: 12px;
}

/* 开关（st.toggle）开启时的轨道底色。
   这里是**有意偏离主色调**的一处：开关用蓝色 #3b7dd8，与棕褐 #8b6f47 区分开，
   便于一眼看出开关状态。

   选择器说明（踩坑记录）：
   Streamlit 的开关轨道没有稳定的 class，只能靠结构定位。轨道 = 一个 div，满足
   「有直接子 div（旋钮）」且「内部不含文字标签 stWidgetLabel」。
   最初写的是「唯一子 div 且该子 div 无后代」，实测**匹配不上**（轨道里除旋钮外
   还有别的东西），页面上开关一直是主题默认色。现在的写法放宽了子元素约束，
   同时用 `:not(:has([data-testid="stWidgetLabel"]))` 排除掉外层整行容器，
   不会把整块背景染蓝。

   另：`.streamlit/config.toml` 里同时把 primaryColor 设成了同一个蓝色。
   两条路走同一个颜色是**刻意**的 —— 主题色是 Streamlit 唯一有保证的钩子，
   CSS 规则是防止主题被改回时的兜底，两者颜色一致，谁生效都不会出现串色。 */
section[data-testid="stSidebar"] [data-testid="stCheckbox"]:has(input:checked)
    div:not([data-testid]):has(> div:not([data-testid]):last-child):not(:has([data-testid="stWidgetLabel"])) {
    background-color: #3b7dd8 !important;
    border-color: #3b7dd8 !important;
}
/* primaryColor 会把输入框/下拉框的聚焦光晕也染成蓝色。
   为了让蓝色只出现在开关上，这里把聚焦光晕改回主色调。 */
.stApp [data-baseweb="input"]:focus-within,
.stApp [data-baseweb="textarea"]:focus-within,
.stApp [data-baseweb="select"] > div:focus-within,
.stApp [data-testid="stChatInput"]:focus-within {
    border-color: #8b6f47 !important;
    box-shadow: 0 0 0 2px rgba(139, 111, 71, .28) !important;
}
/* 服务状态标题着色：正常 #5a7a4a、降级或异常 #a02828。
   Streamlit 的 expander 标签不接受 HTML，因此改为把 expander 包在带 key 的容器里，
   用 st-key-<key> 类选择器着色 —— 仍然是纯 CSS，没有替换组件。 */
.st-key-exp_status_ok [data-testid="stExpander"] summary,
.st-key-exp_status_ok [data-testid="stExpander"] summary p {
    color: #5a7a4a !important;
}
.st-key-exp_status_bad [data-testid="stExpander"] summary,
.st-key-exp_status_bad [data-testid="stExpander"] summary p {
    color: #a02828 !important;
}

/* 开关右侧文字 13px */
.stApp [data-testid="stCheckbox"] [data-testid="stWidgetLabel"] p,
.stApp [data-testid="stCheckbox"] [data-testid="stWidgetLabel"] div {
    font-size: 13px !important; color: #2c2c2c !important;
}

/* 进度条：3px 高横条，底 #e0d8c8、填 #8b6f47 */
.stApp [data-testid="stProgress"] > div > div {
    background-color: #e0d8c8 !important;
    height: 3px !important;
    border-radius: 2px !important;
}
.stApp [data-testid="stProgress"] > div > div > div {
    background-color: #8b6f47 !important;
    height: 3px !important;
    border-radius: 2px !important;
}
.stApp [data-testid="stProgress"] p,
.stApp [data-testid="stProgress"] span {
    font-size: 12px !important; color: #6b5f3f !important;
}

/* 提示框：去掉 Streamlit 默认的彩色底与阴影，统一到米白卡片 */
.stApp [data-testid="stAlert"],
.stApp [data-testid="stAlertContainer"] {
    background: #faf8f2;
    border: 1px solid #e0d8c8;
    border-radius: 4px;
    box-shadow: none;
    color: #2c2c2c;
}
.stApp [data-testid="stAlert"] p,
.stApp [data-testid="stAlertContainer"] p {
    font-size: 13px !important; line-height: 1.7 !important; color: #2c2c2c !important;
}

/* 输入区：白底、#d0c8b8 边框 */
.stApp [data-testid="stBottom"] > div,
.stApp [data-testid="stBottomBlockContainer"] { background: #fbf8f2; }
.stApp [data-testid="stChatInput"] {
    background: #ffffff;
    border: 1px solid #d0c8b8;
    border-radius: 4px;
    box-shadow: none;
}
.stApp [data-testid="stChatInput"] textarea {
    font-size: 15px; line-height: 1.75; color: #2c2c2c; background: transparent;
}
.stApp [data-testid="stChatInput"] textarea::placeholder { color: #8a7a5a; }
.stApp [data-testid="stChatInput"] button,
.stApp [data-testid="stChatInputSubmitButton"] {
    background: transparent; color: #8b6f47; box-shadow: none;
}
.stApp [data-testid="stChatInput"] button:hover,
.stApp [data-testid="stChatInputSubmitButton"]:hover {
    background: #f0ead8; color: #6b5f3f;
}

/* ================= 图标字体还原（必须放在所有 font-family 规则之后） =================
   Streamlit 的图标是「把图标名当文本渲染、靠字体连字(ligature)显示成符号」：
   span 的内容其实是 `keyboard_arrow_down` 这样的名字，只有配上
   font-family: "Material Symbols Rounded" 才会显示成箭头。
   上面任何一条通用的字体规则（尤其是给 span/div 设字体的）都会抢掉连字字体，
   页面上就会直接印出 `keyboard_arrow_down` —— 本项目历史上出现过这个问题。

   写法说明：
   - 必须 !important：Streamlit 用 CSS-in-JS 注入样式，注入顺序不由我们控制。
   - 不要覆盖 color，否则会把图标自身配色（展开箭头是灰色）也改掉。
   - 额外补了侧边栏作用域的重复选择器：侧边栏内的规则优先级较高，
     裸选择器会被压制，加前缀后层级更高。
   - 字体资源随 Streamlit 包自带，无需联网。 */
.stApp [data-testid="stIconMaterial"],
.stApp [data-testid="stExpanderIcon"],
.stApp span.material-symbols-rounded,
.stApp section[data-testid="stSidebar"] [data-testid="stIconMaterial"],
.stApp section[data-testid="stSidebar"] [data-testid="stExpanderIcon"],
.stApp section[data-testid="stSidebar"] span.material-symbols-rounded {
    font-family: "Material Symbols Rounded" !important;
    font-weight: normal !important; font-style: normal !important;
    letter-spacing: normal !important; text-transform: none !important;
    white-space: nowrap !important; direction: ltr !important;
    -webkit-font-smoothing: antialiased;
}
"""


def inject_theme() -> None:
    """注入唯一一份视觉样式；改外观只改 _CSS，不替换组件。"""
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)


def render_hero() -> None:
    """渲染顶部标题区：纯文字 div，不依赖图片。"""
    st.markdown(
        f"""
        <div class="app-hero">
            <div class="app-hero__title">{html.escape(settings.frontend.page_title)}</div>
            <div class="app-hero__sub">混合检索（BGE + BM25 + RRF）· Rerank 精排 · 引用溯源</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


#: 引用编号 ``[1]``、``[2,3]``、``[1-3]``、``[1、2]`` 等写法都要命中。
#: 负向前瞻 ``(?!\()`` 排掉 markdown 链接 ``[1](url)``，避免给链接文字染色。
_CITE_RE = re.compile(r"\[(\d+(?:\s*[-–,，、]\s*\d+)*)\](?!\()")


def highlight_citations(text: str) -> str:
    return _CITE_RE.sub(lambda m: f'<span class="md-cite">[{m.group(1)}]</span>', text)


TIMEOUT = settings.frontend.request_timeout


class BackendError(RuntimeError):

def _url(path: str) -> str:
    return f"{settings.frontend.api_base_url.rstrip('/')}{path}"


def api_health() -> dict[str, Any]:
    r = requests.get(_url("/health"), timeout=15)
    r.raise_for_status()
    return r.json()


def api_strategies() -> dict[str, Any]:
    r = requests.get(_url("/strategies"), timeout=15)
    r.raise_for_status()
    return r.json()


def api_query(question: str, strategy: str, use_stream: bool = True) -> Iterator[dict[str, Any]]:

    payload = {"question": question, "strategy": strategy}

    if not use_stream:
        try:
            r = requests.post(_url("/query"), json=payload, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise BackendError(f"无法连接后端（{settings.frontend.api_base_url}）：{exc}") from exc
        if r.status_code != 200:
            raise BackendError(_extract_error(r))
        yield {"type": "done", "response": r.json()}
        return
    try:
        resp = requests.post(
            _url("/query/stream"), json=payload, timeout=TIMEOUT, stream=True,
            headers={"Accept": "text/event-stream"},
        )
    except requests.RequestException as exc:
        raise BackendError(f"无法连接后端（{settings.frontend.api_base_url}）：{exc}") from exc

    if resp.status_code != 200:
        raise BackendError(_extract_error(resp))

    got_any = False
    try:
        for raw in resp.iter_lines(decode_unicode=False):
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            got_any = True
            yield event
    finally:
        resp.close()

    if not got_any:
        raise BackendError("流式响应为空（后端可能未启用流式接口）")


def _extract_error(resp: requests.Response) -> str:
    """从错误响应里提取可读信息。"""
    try:
        body = resp.json()
        detail = body.get("detail", body)
        if isinstance(detail, list):  # pydantic 校验错误
            detail = "; ".join(
                f"{'.'.join(str(x) for x in d.get('loc', []))}: {d.get('msg', '')}" for d in detail
            )
        return f"HTTP {resp.status_code}: {detail}"
    except Exception:  # noqa: BLE001
        return f"HTTP {resp.status_code}: {resp.text[:200]}"


def api_upload(file_bytes: bytes, filename: str, force: bool = False) -> dict[str, Any]:
    r = requests.post(
        _url("/ingest"),
        files={"file": (filename, file_bytes, "application/pdf")},
        data={"force": str(force).lower()},
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        raise BackendError(_extract_error(r))
    return r.json()


def api_ingest_status(task_id: str) -> dict[str, Any]:
    r = requests.get(_url(f"/ingest/status/{task_id}"), timeout=30)
    r.raise_for_status()
    return r.json()


st.set_page_config(
    page_title=settings.frontend.page_title,
    # 不使用 emoji 作图标；配置里是 Streamlit 的 Material 符号（字体连字，非 emoji）
    page_icon=settings.frontend.page_icon,
    layout=settings.frontend.layout,
)

inject_theme()

if "messages" not in st.session_state:
    st.session_state.messages = []      # [{role, content, citations, docs, latency, stats}]
if "show_debug" not in st.session_state:
    st.session_state.show_debug = settings.frontend.show_debug_panel



def render_user_bubble(text: str) -> None:
    """用户消息：右对齐 div，不显示头像。"""
    st.markdown(
        '<div class="msg-user">'
        '<div class="msg-label">提问</div>'
        f'<div class="msg-user__body">{html.escape(text)}</div>'
        "</div>",
        unsafe_allow_html=True,
    )


def render_citations(citations: dict[str, Any]) -> None:

    items = citations.get("citations", [])
    if not items and not citations.get("is_refusal"):
        return

    with st.expander(f"引用来源（{len(items)} 条）", expanded=True):
        if citations.get("invalid_indices"):
            st.markdown(
                f'<div class="halluc-note">模型引用了不存在的编号 '
                f'{html.escape(str(citations["invalid_indices"]))}，可能存在幻觉，请谨慎采信</div>',
                unsafe_allow_html=True,
            )
            st.warning("引用编号越界：答案中的部分论断缺少可核对的依据。")

        if not items:
            st.caption("（本次回答未引用任何检索片段）")

        for c in items:
            idx = c.get("index", "")
            source = html.escape(str(c.get("source", "")))
            page = html.escape(str(c.get("page", "")))
            score = c.get("score")
            score_txt = (
                f'<span class="cite-meta"> · 相关度 {score:.4f}</span>'
                if isinstance(score, (int, float)) else ""
            )
            st.markdown(
                '<div class="cite-item">'
                f'<span class="cite-idx">[{html.escape(str(idx))}]</span> '
                f'<span class="cite-file">{source}</span>'
                f'<span class="cite-meta"> · 第 {page} 页</span>{score_txt}'
                "</div>",
                unsafe_allow_html=True,
            )
            if c.get("text"):
                st.markdown(
                    f'<div class="cite-text">{html.escape(str(c["text"]))}</div>',
                    unsafe_allow_html=True,
                )


def render_debug(docs: list[dict[str, Any]], latency: dict[str, Any],
                 stats: dict[str, Any] | None = None) -> None:
    """渲染检索调试面板：左列文档块、右列耗时与召回统计。"""
    col1, col2 = st.columns([3, 2])

    with col1:
        st.markdown('<div class="dbg-title">检索到的文档块</div>', unsafe_allow_html=True)
        if not docs:
            st.caption("（无）")
        for d in docs:
            score = d.get("score", 0.0)
            rerank = d.get("rerank_score")
            fused = d.get("fused_score")
            bits = [f"score={score:.4f}"]
            if rerank is not None:
                bits.append(f"精排={rerank:.4f}")
            if fused is not None:
                bits.append(f"融合={fused:.5f}")
            matched = d.get("matched_by") or []
            if matched:
                bits.append("命中=" + "+".join(matched))
            st.markdown(
                f'<div class="dbg-doc"><b>{html.escape(str(d.get("rank", "")))}. '
                f'{html.escape(str(d.get("source", "")))} 第 '
                f'{html.escape(str(d.get("page", "")))} 页</b></div>'
                f'<div class="dbg-meta">{html.escape(" ｜ ".join(bits))}</div>',
                unsafe_allow_html=True,
            )
            text = str(d.get("text", ""))
            st.caption(text[:180] + ("…" if len(text) > 180 else ""))

    with col2:
        st.markdown('<div class="dbg-title">耗时分解</div>', unsafe_allow_html=True)
        rows = {
            "召回": latency.get("recall_ms"),
            "精排": latency.get("rerank_ms"),
            "生成": latency.get("generate_ms"),
            "总计": latency.get("total_ms"),
        }
        lines = [
            f'{k} {v / 1000:.2f}s' for k, v in rows.items()
            if isinstance(v, (int, float)) and v > 0
        ]
        if lines:
            st.markdown(
                "".join(f'<div class="dbg-line">{html.escape(x)}</div>' for x in lines),
                unsafe_allow_html=True,
            )
        else:
            st.caption("（无）")

        if stats:
            st.markdown('<div class="dbg-title">两路召回统计</div>', unsafe_allow_html=True)
            stat_lines = [
                f'向量 {stats.get("dense", "-")} ｜ BM25 {stats.get("sparse", "-")}',
                f'两路重叠 {stats.get("overlap", "-")}',
                f'仅向量召回 {stats.get("dense_only", "-")} ｜ 仅 BM25 {stats.get("sparse_only", "-")}',
            ]
            st.markdown(
                "".join(f'<div class="dbg-line">{html.escape(x)}</div>' for x in stat_lines),
                unsafe_allow_html=True,
            )
            st.caption("重叠越少，说明两路互补性越强 —— 这正是混合检索的价值所在")


def render_assistant_extras(msg: dict[str, Any]) -> None:
    """回答下方的耗时行、引用来源与调试面板。"""
    lat = msg.get("latency") or {}
    parts = []
    if lat.get("retrieve_ms"):
        parts.append(f"检索 {lat['retrieve_ms'] / 1000:.2f}s")
    if lat.get("generate_ms"):
        parts.append(f"生成 {lat['generate_ms'] / 1000:.2f}s")
    if lat.get("total_ms"):
        parts.append(f"总计 {lat['total_ms'] / 1000:.2f}s")
    if parts:
        st.caption(" ｜ ".join(parts) + f" ｜ 策略 {msg.get('strategy', '')}")

    render_citations(msg.get("citations") or {})
    if st.session_state.show_debug:
        with st.expander("检索调试面板", expanded=False):
            render_debug(msg.get("docs") or [], lat, msg.get("stats"))

# 关掉自动多页导航（.streamlit/config.toml）+ 压缩间距（见 _CSS）+ 低频内容进 expander，一屏显示全部模块。
with st.sidebar:
    try:
        strategy_data = api_strategies()
        options = {item["value"]: item for item in strategy_data["retrieval"]}
    except Exception:  # noqa: BLE001 - 后端未启动时用内置默认值，保证界面可打开
        options = {
            "vector": {"label": "向量检索（仅语义）", "description": "BGE 稠密向量检索"},
            "bm25": {"label": "BM25（仅关键词）", "description": "词频检索，精确匹配强"},
            "hybrid": {"label": "混合检索（向量 + BM25 + RRF）", "description": "两路并行融合"},
            "hybrid_rerank": {"label": "混合检索 + Rerank 精排（推荐）", "description": "完整链路"},
        }

    values = list(options)
    default_index = values.index("hybrid_rerank") if "hybrid_rerank" in values else 0
    st.markdown("#### 检索策略")
    strategy = st.selectbox(
        "检索策略",
        options=values,
        index=default_index,
        format_func=lambda v: options[v]["label"],
        label_visibility="collapsed",
        help="切换策略可直观对比检索效果（用于消融演示）",
    )
    st.caption(options[strategy]["description"])

    # 输出与调试开关并排，省一行高度
    col_a, col_b = st.columns(2)
    with col_a:
        use_stream = st.toggle("流式", value=True, key="toggle_stream",
                               help="逐字显示，首字更快")
    with col_b:
        st.session_state.show_debug = st.toggle(
            "调试面板", value=st.session_state.show_debug, key="toggle_debug",
            help="展示文档块、得分、两路召回统计与分步耗时",
        )
    connected = True
    try:
        health = api_health()
    except Exception as exc:  # noqa: BLE001
        health = {}
        connected = False
        health_error = str(exc)[:150]

    if connected:
        status_raw = health.get("status", "unknown")
        status_text = {"ok": "正常", "degraded": "降级", "error": "异常"}.get(status_raw, status_raw)
        # 用 keyed container 给标题着色，两套颜色见 _CSS 的 exp_status_* 规则
        status_key = "exp_status_ok" if status_raw == "ok" else "exp_status_bad"
    else:
        status_text = "未连接"
        status_key = "exp_status_bad"

    with st.container(key=status_key):
        with st.expander(f"服务状态 · {status_text}", expanded=False):
            if not connected:
                st.markdown(
                    f'<div class="dbg-line">{html.escape(settings.frontend.api_base_url)}</div>'
                    f'<div class="dbg-line">{html.escape(health_error)}</div>',
                    unsafe_allow_html=True,
                )
            else:
                idx = health.get("index") or {}
                info_lines = [
                    f"状态 {status_text}",
                    f"模型 {settings.llm.model}",
                    f"版本 v{health.get('version', '?')}",
                ]
                if not health.get("warmed_up", True):
                    info_lines.append("预热 进行中")
                if idx:
                    info_lines.append(
                        f"索引 {idx.get('chunk_strategy')} ｜ 分块 {idx.get('chunks_in_file')}"
                    )
                    info_lines.append(
                        f"向量 {idx.get('vector_count')} ｜ BM25 {idx.get('bm25_count')}"
                    )
                st.markdown(
                    "".join(f'<div class="dbg-line">{html.escape(x)}</div>' for x in info_lines),
                    unsafe_allow_html=True,
                )
                for comp in health.get("components", []):
                    mark = "就绪" if comp.get("ready") else "未就绪"
                    st.markdown(
                        f'<div class="dbg-line">{html.escape(str(comp.get("name", "")))}'
                        f' · {mark} · {html.escape(str(comp.get("detail", ""))[:46])}</div>',
                        unsafe_allow_html=True,
                    )
    with st.expander("上传 PDF 入库", expanded=False):
        uploaded = st.file_uploader("选择 PDF", type=["pdf"], accept_multiple_files=False,
                                    label_visibility="collapsed")
        force = st.checkbox("强制重新解析", value=False,
                            help="忽略文件指纹，重新解析所有文件")
        if st.button("开始入库", disabled=uploaded is None, use_container_width=True):
            try:
                result = api_upload(uploaded.getvalue(), uploaded.name, force=force)
                task_id = result.get("task_id")
                if task_id:
                    bar = st.progress(0.0, text="入库中…")
                    stt: dict[str, Any] = {"status": "running", "stage": "", "progress": 0.0}
                    for _ in range(600):  # 最多等 10 分钟
                        time.sleep(1)
                        stt = api_ingest_status(task_id)
                        bar.progress(min(stt["progress"], 1.0),
                                     text=f"{stt['stage']}（{stt['progress'] * 100:.0f}%）")
                        if stt["status"] in ("done", "failed"):
                            break
                    if stt["status"] == "done":
                        bar.progress(1.0, text="入库完成")
                        st.markdown(
                            f'<div class="dbg-line">完成：{stt["total_chunks"]} 块，'
                            f'耗时 {stt["seconds"]:.1f}s</div>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.error(f"入库失败：{stt.get('error', '未知错误')}")
                else:
                    st.markdown(
                        f'<div class="dbg-line">{html.escape(str(result.get("message", "已提交")))}</div>',
                        unsafe_allow_html=True,
                    )
            except BackendError as exc:
                st.error(str(exc))
    with st.expander("检索链路参数", expanded=False):
        p = settings.retrieval
        rk = settings.rerank
        params = [
            f"每路召回 {p.dense_top_k} / {p.sparse_top_k}",
            f"融合 {p.fusion}（k={p.rrf_k}）｜ 候选池 {p.top_k}",
            f"精排 top_k={rk.top_k} ｜ max_length={rk.max_length}",
            f"上下文预算 {settings.generation.context_max_chars} 字",
            f"分块策略 {settings.chunk.default_strategy}",
        ]
        st.markdown(
            "".join(f'<div class="dbg-line">{html.escape(x)}</div>' for x in params),
            unsafe_allow_html=True,
        )

    if st.button("清空对话", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

render_hero()
st.caption(
    f"领域：{settings.domain} · 检索策略：{strategy} · 后端：{settings.frontend.api_base_url}"
)


for i, msg in enumerate(st.session_state.messages):
    if msg["role"] == "user":
        render_user_bubble(msg["content"])
    else:
        with st.container(key=f"ai-{i}"):
            st.markdown('<div class="msg-label">回答</div>', unsafe_allow_html=True)
            st.markdown(highlight_citations(msg["content"]), unsafe_allow_html=True)
            render_assistant_extras(msg)


prompt = st.chat_input("请输入你的问题…")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    render_user_bubble(prompt)

    meta: dict[str, Any] = {}
    answer = ""
    final: dict[str, Any] = {}
    error: str | None = None

    with st.container(key="ai-live"):
        # 用文字标签替代 loading 与头像：检索阶段显示进度，拿到 meta 后恢复为"回答"
        label_box = st.empty()
        label_box.markdown('<div class="msg-label">回答 · 正在检索…</div>', unsafe_allow_html=True)
        answer_box = st.empty()
        answer_box.markdown("_生成中…_")

        try:
            for event in api_query(prompt, strategy, use_stream=use_stream):
                etype = event.get("type")
                if etype == "meta":
                    meta = event
                    docs = event.get("retrieved_docs") or []
                    label_box.markdown(
                        f'<div class="msg-label">回答 · 已命中 {len(docs)} 个片段，生成中…</div>',
                        unsafe_allow_html=True,
                    )
                elif etype == "delta":
                    answer += event.get("delta", "")
                    answer_box.markdown(
                        highlight_citations(answer) + ' <span class="cursor">▌</span>',
                        unsafe_allow_html=True,
                    )
                elif etype == "done":
                    final = event.get("response") or {}
                    answer = final.get("answer", answer)
                    answer_box.markdown(highlight_citations(answer), unsafe_allow_html=True)
                    label_box.markdown('<div class="msg-label">回答</div>', unsafe_allow_html=True)
                elif etype == "error":
                    error = event.get("error", "未知错误")
        except BackendError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"

        if error:
            label_box.markdown('<div class="msg-label">回答 · 出错</div>', unsafe_allow_html=True)
            answer_box.error(error)
            st.session_state.messages.append(
                {"role": "assistant", "content": f"调用失败：{error}", "citations": {}}
            )
        else:
            label_box.markdown('<div class="msg-label">回答</div>', unsafe_allow_html=True)
            citations = final.get("citations") or {}
            latency = final.get("latency") or meta.get("latency") or {}
            docs = final.get("retrieved_docs") or meta.get("retrieved_docs") or []
            stats = meta.get("stats") or {}
            for w in (final.get("warnings") or []) + (meta.get("warnings") or []):
                st.warning(w)

            record = {
                "role": "assistant",
                "content": answer,
                "citations": citations,
                "docs": docs,
                "latency": latency,
                "stats": stats,
                "strategy": final.get("strategy") or meta.get("strategy") or strategy,
            }
            render_assistant_extras(record)
            st.session_state.messages.append(record)
