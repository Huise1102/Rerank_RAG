import argparse
import ast
import io
import json
import os
import re
import sys
import tokenize

SKIP_DIRS = {
    "node_modules", ".venv", "venv", "__pycache__", ".git", ".uv-cache", ".hf-cache",
    ".mplcache", ".pytest_cache", ".idea", ".run", "dist", "build", "indexes", "logs",
    "storage", "coverage", ".next", ".turbo",
}
CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".vue", ".css", ".scss", ".less"}
HASH_EXT = {".ini", ".yml", ".yaml", ".toml", ".conf", ".cfg", ".sh"}


def is_hash_file(path):
    name = os.path.basename(path)
    return (
        os.path.splitext(path)[1].lower() in HASH_EXT
        or name.startswith(".env")
        or name.startswith("Dockerfile")
        or name in {"Makefile", ".dockerignore"}
    )

BANNED_CHARS = set("=→←↑↓★☆●○◆◇■□─━│┌┐└┘├┤┬┴┼═║►▶◄◀•※§¶@$^&~`\\|<>#*")

META_PHRASES = [
    "按照要求", "按要求", "根据要求", "按需求", "根据需求", "需求文档", "用户要求",
    "用户反馈", "根据反馈", "本次新增", "本次修改", "本次调整", "本次", "已修复",
    "修复了", "修复后", "此前", "之前的实现", "原来", "以前", "遗留问题", "临时方案",
    "临时", "TODO", "FIXME", "待办", "后续", "将来", "待重构", "按约定", "保持一致",
    "为了满足", "按提示", "提示词", "用户希望", "已按", "改为",
]

DIRECTIVE_RE = re.compile(
    r"^(?:#!|#\s*(?:-\*-|noqa|type:|pragma:|fmt:|isort:|pylint:|mypy:|ruff:|nosec)|"
    r"//\s*(?:eslint|prettier|@ts-)|/\*\s*(?:eslint|@ts-|prettier))",
    re.IGNORECASE,
)

JSDOC_RE = re.compile(
    r"^@(?:param|returns?|typedef|type|description|example|deprecated|see|throws?|"
    r"augments|ignore|property|default)\b",
    re.IGNORECASE,
)


def strip_markers(text):
    text = text.strip()
    text = re.sub(r"^[/#*!\s]+", "", text)
    text = re.sub(r"[*\s]+$", "", text)
    return text.strip()


def is_directive(text):
    return bool(DIRECTIVE_RE.match(text.strip()))


def scan_cstyle(path):
    try:
        src = io.open(path, encoding="utf-8").read()
    except UnicodeDecodeError:
        src = io.open(path, encoding="gbk", errors="ignore").read()
    results = []
    in_block = False
    for lineno, line in enumerate(src.splitlines(), 1):
        i = 0
        quote = None
        escaped = False
        start = None
        while i < len(line):
            ch = line[i]
            nxt = line[i + 1] if i + 1 < len(line) else ""
            if in_block:
                if ch == "*" and nxt == "/":
                    results.append((lineno, line[start:i]))
                    in_block = False
                    i += 2
                    start = None
                    continue
                i += 1
                continue
            if quote:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    quote = None
                i += 1
                continue
            if ch in "\"'`":
                quote = ch
                i += 1
                continue
            if ch == "/" and nxt == "*":
                in_block = True
                start = i + 2
                i += 2
                continue
            if ch == "/" and nxt == "/":
                results.append((lineno, line[i + 2:]))
                break
            i += 1
        if in_block and start is not None:
            results.append((lineno, line[start:]))
            start = 0
    if path.lower().endswith((".vue", ".html")):
        for lineno, line in enumerate(src.splitlines(), 1):
            for match in re.finditer(r"<!--(.*?)-->", line):
                results.append((lineno, match.group(1)))
    return results


def scan_python(path, include_docstrings=True):
    results = []
    with open(path, "rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type == tokenize.COMMENT:
                results.append((tok.start[0], tok.string))
    if include_docstrings:
        try:
            source = io.open(path, encoding="utf-8").read()
            tree = ast.parse(source)
        except Exception:  # noqa: BLE001
            return results
        doc_nodes = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        for node in ast.walk(tree):
            if isinstance(node, doc_nodes) and node.body:
                first = node.body[0]
                doc = ast.get_docstring(node, clean=False)
                if doc and isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    results.append((first.lineno, doc))
    return results


def evaluate(text):
    body = strip_markers(text)
    if not body:
        return []
    if is_directive(text) or is_directive(body) or is_directive("// " + body):
        return ["directive"]
    flags = []
    check_body = body
    tag = JSDOC_RE.match(check_body)
    if tag:
        check_body = check_body[tag.end():]
    if check_body.startswith(":"):
        flags.append("stray_marker")
    if not re.search(r"[\u4e00-\u9fff]", check_body):
        flags.append("no_cjk")
    if any(ch in BANNED_CHARS for ch in check_body):
        flags.append("banned")
    if re.search(r"([=\-*#/~_|─])\1{2,}", check_body):
        flags.append("decoration")
    if any(p in check_body for p in META_PHRASES):
        flags.append("meta")
    return flags


def scan_hash(path):
    try:
        src = io.open(path, encoding="utf-8").read()
    except UnicodeDecodeError:
        src = io.open(path, encoding="gbk", errors="ignore").read()
    results = []
    for lineno, line in enumerate(src.splitlines(), 1):
        text = line.strip()
        if text.startswith("#"):
            results.append((lineno, text))
    return results


def collect_files(targets):
    files = []
    for target in targets:
        if os.path.isfile(target):
            files.append(target)
            continue
        for dirpath, dirnames, filenames in os.walk(target):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                path = os.path.join(dirpath, name)
                if os.path.splitext(name)[1].lower() in CODE_EXT or is_hash_file(path):
                    files.append(path)
    return sorted(files)


def main():
    parser = argparse.ArgumentParser(description="扫描代码注释违规项")
    parser.add_argument("targets", nargs="*", default=["."], help="待扫描目录或文件")
    parser.add_argument("--json", dest="json_path", help="输出 JSON 报告路径")
    parser.add_argument("--strict", action="store_true", help="存在违规时返回非零退出码")
    parser.add_argument("--skip-directives", action="store_true", help="报告中忽略指令类注释")
    args = parser.parse_args()

    rows = []
    parse_errors = []
    for path in collect_files(args.targets):
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".py":
                comments = scan_python(path)
            elif is_hash_file(path):
                comments = scan_hash(path)
            else:
                comments = scan_cstyle(path)
        except Exception as exc:  # noqa: BLE001
            parse_errors.append((path, str(exc)))
            continue
        for lineno, text in comments:
            flags = evaluate(text)
            rows.append({
                "file": os.path.abspath(path),
                "line": lineno,
                "text": text.strip(),
                "flags": flags,
            })

    flagged = [r for r in rows if r["flags"] and "directive" not in r["flags"]]
    stats = {
        "comments": len(rows),
        "directives": sum(1 for r in rows if "directive" in r["flags"]),
        "violations": len(flagged),
        "banned": sum(1 for r in flagged if "banned" in r["flags"]),
        "no_cjk": sum(1 for r in flagged if "no_cjk" in r["flags"]),
        "meta": sum(1 for r in flagged if "meta" in r["flags"]),
        "decoration": sum(1 for r in flagged if "decoration" in r["flags"]),
        "parse_errors": len(parse_errors),
    }

    print(json.dumps(stats, ensure_ascii=False))
    current = None
    for row in flagged:
        if args.skip_directives and "directive" in row["flags"]:
            continue
        if row["file"] != current:
            current = row["file"]
            print("\n%s" % current)
        print("  %d [%s] %s" % (row["line"], ",".join(row["flags"]), row["text"]))

    for path, error in parse_errors:
        print("解析失败 %s: %s" % (path, error), file=sys.stderr)

    if args.json_path:
        with io.open(args.json_path, "w", encoding="utf-8") as fh:
            json.dump({"stats": stats, "rows": rows}, fh, ensure_ascii=False, indent=2)

    return 1 if args.strict and flagged else 0


if __name__ == "__main__":
    sys.exit(main())
